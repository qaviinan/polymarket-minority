"""Row-level check of the HuggingFace Polymarket_data release against our Goldsky ingests.

Samples random windows from Stage 1 (R2 ``order_filled/``, Oct-Nov 2024) and Stage 2 (local
mirror, Sep-Oct 2026), streams only those time ranges from HF ``users.parquet`` and
``orderfilled.parquet`` (row-group pruning on ``timestamp``), and compares fills joined on
``(transaction_hash, log_index)``. Only the filtered samples are written locally.

    python scripts/validate_hf_sample.py            # 2 x 6h windows per stage
    VALIDATE_WINDOWS=3 VALIDATE_HOURS=4 python scripts/validate_hf_sample.py
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.lib.duck import get_duck, r2_bucket  # noqa: E402

HF = "https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data/resolve/main/"
COMBOS = "0xe3333700ca9d93003f00f0f71f8515005f6c00aa"
EXCHANGES = (
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xe2222d279d744050d28e00520010520000310f59",
)
STAGE2_MIRROR = "data/periods/recent_2026/r2_mirror"
OUT = Path("data/validation")
LOG = Path("logs/validation")

STAGES = {
    "stage1_2024": (datetime(2024, 10, 1, tzinfo=timezone.utc), datetime(2024, 11, 5, tzinfo=timezone.utc)),
    "stage2_2026": (datetime(2026, 9, 21, 9, tzinfo=timezone.utc), datetime(2026, 10, 4, 21, tzinfo=timezone.utc)),
}


def pick_windows(n: int, hours: int, seed: int) -> list[tuple[str, int, int]]:
    rng = random.Random(seed)
    out = []
    for stage, (lo, hi) in STAGES.items():
        lo_s, hi_s = int(lo.timestamp()), int(hi.timestamp()) - hours * 3600
        starts: list[int] = []
        while len(starts) < n:
            s = lo_s + rng.randrange((hi_s - lo_s) // 3600) * 3600
            if all(abs(s - t) >= hours * 3600 for t in starts):
                starts.append(s)
        out += [(stage, s, s + hours * 3600) for s in sorted(starts)]
    return out


def goldsky_rows(con, stage: str, t0: int, t1: int) -> pd.DataFrame:
    if stage == "stage1_2024":
        days = pd.date_range(pd.to_datetime(t0, unit="s").normalize(), pd.to_datetime(t1 - 1, unit="s").normalize())
        src = "[" + ",".join(f"'r2://{r2_bucket()}/order_filled/dt={d.date()}/*.parquet'" for d in days) + "]"
    else:
        src = f"'{STAGE2_MIRROR}/order_filled/**/*.parquet'"
    return con.execute(
        f"""
        SELECT lower(transaction_hash) AS transaction_hash,
               CAST(split_part(id, '_', 3) AS INTEGER) AS log_index,
               block_number, block_timestamp AS timestamp, lower(address) AS contract_addr,
               lower(user_id) AS user_id, order_type, side, asset, amount_usdc, amount_shares,
               price, fee,
               CASE WHEN order_hash LIKE '0x%' THEN lower(order_hash) ELSE '0x' || lower(order_hash) END AS order_hash, lower(counterparty_id) AS counterparty_id
        FROM read_parquet({src}, hive_partitioning = true, union_by_name = true)
        WHERE block_timestamp >= {t0} AND block_timestamp < {t1}
        """
    ).df()


def hf_rows(con, b0: int, b1: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """HF rows by block range: HF 2024 timestamps are extrapolated and drift, blocks are exact."""
    users = con.execute(
        f"""
        SELECT transaction_hash, log_index, block_number, timestamp, address, role, direction,
               usd_amount, token_amount, price, market_id, nonusdc_side
        FROM read_parquet('{HF}users.parquet')
        WHERE block_number BETWEEN {b0} AND {b1}
        """
    ).df()
    of = con.execute(
        f"""
        SELECT transaction_hash, log_index, contract, order_hash, maker, taker,
               maker_asset_id, taker_asset_id, maker_amount_filled, taker_amount_filled,
               maker_fee, taker_fee, protocol_fee
        FROM read_parquet('{HF}orderfilled.parquet')
        WHERE block_number BETWEEN {b0} AND {b1}
        """
    ).df()
    for col in ("maker_amount_filled", "taker_amount_filled", "maker_fee", "taker_fee", "protocol_fee"):
        of[col] = of[col].map(lambda b: int.from_bytes(b, "little") / 1e6)
    of["usdc_raw"] = of.maker_amount_filled.where(of.maker_asset_id == "0", of.taker_amount_filled)
    of["shares_raw"] = of.taker_amount_filled.where(of.maker_asset_id == "0", of.maker_amount_filled)
    of["asset"] = of.maker_asset_id.where(of.maker_asset_id != "0", of.taker_asset_id)
    return users, of


def universe_assets(con, stage: str, g: pd.DataFrame) -> set[str]:
    """Stage 1 used a fixed token list; Stage 2 used a per-slice exclusion list, so use in-window assets."""
    if stage == "stage1_2024":
        q = f"SELECT DISTINCT token_id FROM read_parquet('r2://{r2_bucket()}/metadata/outcome_tokens.parquet')"
        return set(con.execute(q).df().iloc[:, 0].astype(str))
    return set(g.asset.astype(str))


def compare(g: pd.DataFrame, users: pd.DataFrame, of: pd.DataFrame, assets: set[str]) -> dict:
    hf = users.merge(of, on=["transaction_hash", "log_index"], how="outer", indicator="hf_join")
    combos = int((g.contract_addr == COMBOS).sum())
    g = g[g.contract_addr != COMBOS]
    m = g.merge(hf, on=["transaction_hash", "log_index"], how="left", suffixes=("_g", "_hf"), indicator=True)
    hit = m[m._merge == "both"]

    def frac_eq(a, b, tol=None) -> float:
        if tol is None:
            return float((hit[a].astype(str) == hit[b].astype(str)).mean()) if len(hit) else float("nan")
        return float(((hit[a] - hit[b]).abs() <= tol).mean()) if len(hit) else float("nan")

    hf_in_universe = hf[hf.asset.astype(str).isin(assets) & (hf.hf_join == "both")]
    gk = set(zip(g.transaction_hash, g.log_index))
    extra = hf_in_universe[[k not in gk for k in zip(hf_in_universe.transaction_hash, hf_in_universe.log_index)]]
    return {
        "goldsky_rows": int(len(g)),
        "goldsky_combos_rows_excluded": combos,
        "goldsky_dup_keys": int(g.duplicated(["transaction_hash", "log_index"]).sum()),
        "hf_users_rows": int(len(users)),
        "hf_orderfilled_rows": int(len(of)),
        "hf_users_vs_orderfilled_unmatched": int((hf.hf_join != "both").sum()),
        "recall_goldsky_in_hf": float(len(hit) / len(g)) if len(g) else float("nan"),
        "missing_from_hf": int((m._merge == "left_only").sum()),
        "missing_whose_tx_is_in_hf": int(m[m._merge == "left_only"].transaction_hash.isin(set(hf.transaction_hash)).sum()),
        "missing_on_100_block_boundary": int((m[m._merge == "left_only"].block_number_g % 100 <= 1).sum()),
        "missing_blocks": sorted(int(b) for b in m[m._merge == "left_only"].block_number_g.unique()),
        "hf_rows_on_goldsky_universe": int(len(hf_in_universe)),
        "hf_extra_rows_on_universe": int(len(extra)),
        "hf_extra_usd": float(extra.usd_amount.sum()),
        "hf_extra_whose_tx_is_in_goldsky": int(extra.transaction_hash.isin(set(g.transaction_hash)).sum()),
        "match": {
            "block_number": frac_eq("block_number_g", "block_number_hf", 0),
            "timestamp": frac_eq("timestamp_g", "timestamp_hf", 0),
            "user_id=address": frac_eq("user_id", "address"),
            "order_type=role": frac_eq("order_type", "role"),
            "side=direction": frac_eq("side", "direction"),
            "asset": frac_eq("asset_g", "asset_hf"),
            "amount_usdc=users.usd_amount": frac_eq("amount_usdc", "usd_amount", 1e-6),
            "amount_usdc=users.usd_amount_2dp": frac_eq("amount_usdc", "usd_amount", 0.005 + 1e-9),
            "amount_usdc=orderfilled_raw": frac_eq("amount_usdc", "usdc_raw", 1e-6),
            "amount_shares=users.token_amount": frac_eq("amount_shares", "token_amount", 1e-6),
            "amount_shares=orderfilled_raw": frac_eq("amount_shares", "shares_raw", 1e-6),
            "timestamp_abs_diff_s_max": float((hit.timestamp_g - hit.timestamp_hf).abs().max()) if len(hit) else float("nan"),
            "price": frac_eq("price_g", "price_hf", 1e-6),
            "order_hash": frac_eq("order_hash_g", "order_hash_hf"),
            "counterparty=taker": frac_eq("counterparty_id", "taker"),
            "fee=maker_fee": frac_eq("fee", "maker_fee", 1e-6),
            "fee=maker+taker+protocol": float(
                ((hit.fee - (hit.maker_fee + hit.taker_fee + hit.protocol_fee)).abs() <= 1e-6).mean()
            ) if len(hit) else float("nan"),
        },
        "usd_goldsky": float(g.amount_usdc.sum()),
        "usd_hf_matched": float(hit.usd_amount.sum()),
    }


def main() -> None:
    n = int(os.environ.get("VALIDATE_WINDOWS", "2"))
    hours = int(os.environ.get("VALIDATE_HOURS", "6"))
    seed = int(os.environ.get("VALIDATE_SEED", "20261007"))
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)
    con = get_duck()
    con.execute("SET enable_progress_bar = false;")
    results = []
    for stage, t0, t1 in pick_windows(n, hours, seed):
        label = f"{stage}_{datetime.fromtimestamp(t0, timezone.utc):%Y%m%dT%H}"
        print(f"[{label}] {datetime.fromtimestamp(t0, timezone.utc)} .. {datetime.fromtimestamp(t1, timezone.utc)}", flush=True)
        g = goldsky_rows(con, stage, t0, t1)
        users, of = hf_rows(con, int(g.block_number.min()), int(g.block_number.max()))
        g.to_parquet(OUT / f"{label}_goldsky.parquet")
        users.to_parquet(OUT / f"{label}_hf_users.parquet")
        of.to_parquet(OUT / f"{label}_hf_orderfilled.parquet")
        r = {"window": label, "t0": t0, "t1": t1, **compare(g, users, of, universe_assets(con, stage, g))}
        print(json.dumps(r, indent=1), flush=True)
        results.append(r)
    path = LOG / f"hf_validation_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(results, indent=1))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

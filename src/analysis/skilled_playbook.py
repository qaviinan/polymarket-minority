"""Decompose taker PnL into plays to see where Skilled PnL comes from.

Each taker fill is treated as a long position in the outcome it backs. A BUY at price p backs
that token at p. A SELL at p backs the complementary outcome at 1 - p. Fills are bucketed by

  * effective price band   (near-certain >=95c, strong favorite 80-95c, favorite 50-80c,
                            underdog 20-50c, longshot <20c)
  * timing                 (hours between fill and scheduled market end: after end, <24h,
                            1-7 days, >7 days)
  * theme                  (skilled_profile.classify_theme)

An account's dominant play is the band with the largest share of its positive PnL.
Reads the restricted-universe fills cache built by skilled_profile (same PnL as the 2024 run).

Run: python -m src.analysis.skilled_playbook
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.skilled_profile import CACHE_PATH, LABEL_PATH

OUT_DIR = Path(os.environ.get("SKILLED_PLAYBOOK_OUT_DIR", "logs/skilled_playbook"))
META_PATH = Path("logs/concentrated_winners/market_meta.parquet")
TOP_PATH = Path("logs/skilled_profile/top_skilled.parquet")
GROUP_COL = "trader_group_fdr_q05"
TOP_N = int(os.environ.get("SKILLED_PLAYBOOK_TOP_N", "10"))

BANDS = [0, 0.2, 0.5, 0.8, 0.95, 1.01]
BAND_LABELS = ["longshot <20c", "underdog 20-50c", "favorite 50-80c", "strong fav 80-95c", "near-certain >=95c"]
TIMING_BINS = [-np.inf, 0, 24, 24 * 7, np.inf]
TIMING_LABELS = ["after listed end (live event)", "<24h before end", "1-7 days before", ">7 days before"]


def load() -> pd.DataFrame:
    f = pd.read_parquet(CACHE_PATH)
    f = f[f["order_type"] == "taker"].copy()
    meta = pd.read_parquet(META_PATH)[["market_id", "question", "theme"]]
    labels = pd.read_parquet(LABEL_PATH)[["user_id", GROUP_COL]]
    top = set(pd.read_parquet(TOP_PATH).sort_values("taker_pnl", ascending=False)["user_id"].head(TOP_N))
    f = f.merge(meta, on="market_id", how="left").merge(labels, on="user_id", how="left")
    f["cohort"] = f[GROUP_COL].replace({"Skilled_Winner": "Skilled_rest"})
    f.loc[f["user_id"].isin(top), "cohort"] = "Skilled_top10"
    buy = f["side"].eq("BUY")
    f["eff_price"] = np.where(buy, f["price"], 1 - f["price"])
    signed_sh = np.where(buy, f["amount_shares"], -f["amount_shares"])
    signed_usdc = np.where(buy, f["amount_usdc"], -f["amount_usdc"])
    f["pnl"] = signed_sh * f["payout"] - signed_usdc - f["fee"]
    f["stake"] = f["amount_shares"] * f["eff_price"]
    f["won"] = np.where(buy, f["payout"] > 0.5, f["payout"] < 0.5)
    f["won_shares"] = f["won"] * f["amount_shares"]
    f["band"] = pd.cut(f["eff_price"], BANDS, labels=BAND_LABELS, right=False)
    f["timing"] = pd.cut((f["end_ts"] - f["ts"]) / 3600.0, TIMING_BINS, labels=TIMING_LABELS)
    return f


def breakdown(f: pd.DataFrame, by: str) -> pd.DataFrame:
    g = f.groupby(["cohort", by], observed=True).agg(
        pnl=("pnl", "sum"), stake=("stake", "sum"), volume=("amount_usdc", "sum"),
        fills=("pnl", "size"), won_shares=("won_shares", "sum"), accounts=("user_id", "nunique"),
    ).reset_index()
    shares = f.groupby(["cohort", by], observed=True)["amount_shares"].sum().values
    g["win_rate"] = g["won_shares"] / np.where(shares > 0, shares, np.nan)
    tot = g.groupby("cohort")[["pnl", "volume"]].transform("sum")
    g["pnl_share"] = g["pnl"] / tot["pnl"]
    g["volume_share"] = g["volume"] / tot["volume"]
    g["roi_on_stake"] = g["pnl"] / g["stake"].replace(0, np.nan)
    g["breakeven_win_rate"] = g["stake"] / np.where(shares > 0, shares, np.nan)
    return g


def account_plays(f: pd.DataFrame) -> pd.DataFrame:
    sk = f[f["cohort"].isin(["Skilled_top10", "Skilled_rest"])]
    by = sk.groupby(["user_id", "band"], observed=True)["pnl"].sum().unstack(fill_value=0.0)
    total = by.sum(axis=1)
    dominant = by.clip(lower=0).idxmax(axis=1)
    out = pd.DataFrame({"pnl": total, "dominant_play": dominant})
    out["dominant_share_of_positive"] = by.clip(lower=0).max(axis=1) / by.clip(lower=0).sum(axis=1).replace(0, np.nan)
    out["cohort"] = sk.groupby("user_id")["cohort"].first()
    return out.reset_index()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    f = load()
    tables = {k: breakdown(f, k) for k in ("band", "timing", "theme")}
    cross = (f[f["cohort"].isin(["Skilled_top10", "Skilled_rest"])]
             .groupby(["band", "timing"], observed=True)["pnl"].sum().unstack(fill_value=0.0))
    acc = account_plays(f)
    play_summary = acc.groupby(["cohort", "dominant_play"], observed=True).agg(
        accounts=("user_id", "size"), pnl=("pnl", "sum"), median_pnl=("pnl", "median")).reset_index()
    for k, t in tables.items():
        t.to_csv(OUT_DIR / f"by_{k}.csv", index=False)
    cross.to_csv(OUT_DIR / "skilled_band_x_timing.csv")
    acc.to_csv(OUT_DIR / "skilled_account_plays.csv", index=False)
    play_summary.to_csv(OUT_DIR / "skilled_play_summary.csv", index=False)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    cols = ["cohort", None, "accounts", "fills", "volume", "volume_share", "pnl", "pnl_share", "win_rate",
            "breakeven_win_rate", "roi_on_stake"]
    for k, t in tables.items():
        c = [x if x else k for x in cols]
        print(f"\n=== by {k} ===")
        print(t[c].round(3).to_string(index=False))
    print("\n=== Skilled (all) PnL: price band x timing ===")
    print(cross.round(0).to_string())
    print("\n=== Skilled accounts by dominant play ===")
    print(play_summary.round(1).to_string(index=False))
    (OUT_DIR / "summary.json").write_text(json.dumps({
        "skilled_pnl_total": float(f.loc[f["cohort"].str.startswith("Skilled"), "pnl"].sum()),
    }, indent=2))


if __name__ == "__main__":
    main()

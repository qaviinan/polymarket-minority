"""Score the resolved-market aggregate built from the extended 2026 tape.

The full fill tape does not fit on disk, so ``aggregate_resolved_days.py`` writes one
aggregate per day (taker and maker, combo exchange dropped, payouts from the Hugging Face
markets file). This script combines the days into event-level sign-randomization labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.classification import build_classification_table
from src.analysis.multiple_testing import benjamini_hochberg
from src.analysis.sign_randomization import run_sign_randomization_core


def main() -> None:
    day_dir = Path("data/periods/recent_2026/analysis/user_event_days")
    out_dir = Path("logs/periods/recent_2026/resolved_window")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(day_dir.glob("*.parquet"))
    ua = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
    # One event can trade on several days. Those rows are one flip, not several.
    ua = ua.groupby(["user_id", "event_id", "order_type"], as_index=False).agg(
        pnl=("pnl", "sum"),
        fees=("fees", "sum"),
        volume=("volume", "sum"),
        n_trades=("n_trades", "sum"),
        min_ts=("min_ts", "min"),
        max_ts=("max_ts", "max"),
    )
    # pnl is already net of fees. Pre-fee residual is pnl + fees = shares*payout - usdc.
    pre_fee = float((ua["pnl"] + ua["fees"]).sum())
    volume = float(ua["volume"].sum())
    taker = ua[ua["order_type"].eq("taker")].copy()
    ev = taker.rename(columns={"pnl": "event_pnl", "fees": "event_fees"})[
        ["user_id", "event_id", "event_pnl", "event_fees", "n_trades"]
    ]
    acct = (
        taker.groupby("user_id", as_index=False)
        .agg(
            total_pnl=("pnl", "sum"),
            n_trades=("n_trades", "sum"),
            n_events=("event_id", "nunique"),
            volume=("volume", "sum"),
        )
    )
    summary, peak, wall = run_sign_randomization_core(
        ev, acct, np.random.default_rng(42), 10_000, exact_max_events=12, min_events=5
    )
    labels = build_classification_table(acct, summary)
    winners = (labels["n_trades"] >= 10) & (labels["n_events_sim"] >= 5) & labels["total_pnl"].gt(0)
    labels["bh_q05"] = False
    if winners.any():
        labels.loc[winners, "bh_q05"] = benjamini_hochberg(
            labels.loc[winners, "p_skill"].to_numpy(), 0.05
        )
    demote = labels["trader_group"].eq("Skilled_Winner") & ~labels["bh_q05"]
    labels["trader_group_nominal"] = labels["trader_group"]
    labels.loc[demote, "trader_group"] = "Lucky_Winner"
    labels.to_parquet(out_dir / "account_classification.parquet", index=False)
    counts = labels["trader_group"].value_counts().to_dict()
    skilled = labels[labels["trader_group"].eq("Skilled_Winner")]
    report = {
        "rows": int(len(ua)),
        "fills": int(ua["n_trades"].sum()),
        "ts": [int(ua["min_ts"].min()), int(ua["max_ts"].max())],
        "taker_volume": float(taker["volume"].sum()),
        "participant_volume": volume,
        "pre_fee_residual": pre_fee,
        "pre_fee_residual_bps": 10000.0 * pre_fee / volume if volume else None,
        "accounts": int(len(acct)),
        "simulated": int(len(summary)),
        "unscorable": int(summary["unscorable"].sum()) if len(summary) else 0,
        "p_skill_zero": int((summary["p_skill"] == 0).sum()) if len(summary) else 0,
        "groups": {k: int(v) for k, v in counts.items()},
        "fdr_skilled": int(len(skilled)),
        "min_skilled_events": int(skilled["n_events_sim"].min()) if len(skilled) else None,
        "skilled_pnl": float(skilled["total_pnl"].sum()) if len(skilled) else 0.0,
        "sign_randomization_seconds": wall,
        "sign_randomization_peak_bytes": peak,
    }
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

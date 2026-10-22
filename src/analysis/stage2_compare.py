"""Compare a recent period with Oct-Nov 2024 and check which 2024 Skilled wallets still trade.

Reads the 2024 FDR labels from the top-level ``logs/`` tree (override with
``STAGE2_2024_LABELS``) and the current ``PERIOD_ID`` labels from ``logs/periods/<id>/``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from src.analysis.event_grouping import register_event_views
from src.analysis.partition import register_order_flow_views
from src.lib.debug import debug_step
from src.lib.duck import get_duck, load_env
from src.lib.period import logs_path, period_id


STAGE1_FDR = Path("logs/multiple_testing/account_classification_primary_fdr.parquet")
STAGE1_GATE = Path("logs/multiple_testing/gate_a_statistical.json")
WATCHLIST = Path("logs/concentrated_winners/watchlist_non_election.csv")


def _share(num: int, den: int) -> float:
    return float(num / den) if den else float("nan")


def main() -> None:
    load_env()
    pid = period_id()
    if not pid:
        raise RuntimeError("Set PERIOD_ID (e.g. recent_2026) for Stage 2 comparison")
    s1_path = Path(os.environ.get("STAGE2_2024_LABELS", str(STAGE1_FDR)))
    s2_path = logs_path("multiple_testing", "account_classification_primary_fdr.parquet")
    if not s1_path.exists():
        raise RuntimeError(f"Stage 1 FDR labels missing: {s1_path}")
    if not s2_path.exists():
        raise RuntimeError(f"Run coverage_audit + multiple_testing first: {s2_path}")

    with debug_step("stage2_compare") as dbg:
        s1 = pd.read_parquet(s1_path)
        s2 = pd.read_parquet(s2_path)
        dbg.log("meta", "period_id", pid)
        dbg.log("meta", "stage1_labels", str(s1_path))
        dbg.log("meta", "stage2_labels", str(s2_path))

        def eligible(df: pd.DataFrame) -> pd.DataFrame:
            return df[(df["n_trades"] >= 10) & (df["n_events_sim"] >= 5)]

        def skilled(df: pd.DataFrame, col: str = "trader_group") -> pd.DataFrame:
            return df[df[col] == "Skilled_Winner"]

        e1, e2 = eligible(s1), eligible(s2)
        sk1 = skilled(s1, "trader_group_fdr_q05") if "trader_group_fdr_q05" in s1 else skilled(s1)
        sk1_nom = skilled(s1)
        sk2 = skilled(s2, "trader_group_fdr_q05") if "trader_group_fdr_q05" in s2 else skilled(s2)
        sk2_nom = skilled(s2)

        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        users_any = set(
            con.execute("SELECT DISTINCT lower(user_id) AS user_id FROM order_filled").df()["user_id"]
        )
        try:
            filt = con.execute("SELECT * FROM _universe_filter_stats").df().iloc[0].to_dict()
        except Exception:
            filt = {}
        vol = con.execute(
            """
            SELECT COUNT(*) n_fills, SUM(amount_usdc) usdc,
              MIN(CAST(block_timestamp AS BIGINT)) ts_min,
              MAX(CAST(block_timestamp AS BIGINT)) ts_max
            FROM takers
            """
        ).fetchone()
        cutoff = int(vol[3])
        resolved = con.execute(
            f"""
            SELECT SUM(amount_usdc) FROM takers t
            JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR)=CAST(t.asset AS VARCHAR)
            JOIN market_resolution mr ON mr.market_id=ot.market_id
            WHERE mr.scheduled_end_ts <= {cutoff}
            """
        ).fetchone()[0]

        s1_ids = set(sk1["user_id"].str.lower())
        s1_nom_ids = set(sk1_nom["user_id"].str.lower())
        appear = s1_ids & users_any
        appear_nom = s1_nom_ids & users_any
        s2_by_id = s2.assign(_id=s2["user_id"].str.lower()).set_index("_id")
        retested = appear & set(e2["user_id"].str.lower())
        both_class = appear & set(s2.loc[s2["trader_group"] != "Excluded", "user_id"].str.lower())

        surv = s2_by_id.reindex(list(appear)).dropna(how="all")
        trans = None
        if len(surv):
            left = sk1.assign(_id=sk1["user_id"].str.lower())[["_id"]].merge(
                s2.assign(_id=s2["user_id"].str.lower())[["_id", "trader_group", "trader_group_fdr_q05",
                                                          "total_pnl", "n_trades", "n_events_sim", "p_skill"]],
                on="_id", how="inner",
            )
            trans = pd.crosstab(
                pd.Series(["Skilled_FDR_2024"] * len(left), name="from_2024"),
                left["trader_group_fdr_q05"].fillna(left["trader_group"]).fillna("Absent"),
            )
            dbg.save_diagnostic_df("survival_2024_skilled_in_2026", left)

        watch = {}
        if WATCHLIST.exists():
            wl = pd.read_csv(WATCHLIST)
            wids = set(wl["user_id"].str.lower())
            watch = {
                "watchlist_n": int(len(wids)),
                "appear_in_period": int(len(wids & users_any)),
                "reclassified": int(len(wids & set(s2["user_id"].str.lower()))),
            }
            hit = wl[wl["user_id"].str.lower().isin(users_any)]
            if len(hit):
                dbg.save_diagnostic_df("watchlist_hits", hit)

        comp_path = logs_path("composition_audit", "category_by_group.parquet")
        top_cat = None
        if comp_path.exists():
            cat = pd.read_parquet(comp_path)
            if "volume" in cat.columns and "category" in cat.columns:
                g = cat.groupby("category")["volume"].sum().sort_values(ascending=False)
                top_cat = {"category": str(g.index[0]), "volume_share": float(g.iloc[0] / g.sum())}

        s1_gate = json.loads(STAGE1_GATE.read_text()) if STAGE1_GATE.exists() else {}
        s2_gate_path = logs_path("multiple_testing", "gate_a_statistical.json")
        s2_gate = json.loads(s2_gate_path.read_text()) if s2_gate_path.exists() else {}

        summary = {
            "period_id": pid,
            "window_ts": [int(vol[2] or 0), int(vol[3] or 0)],
            "filter": {k: (int(v) if pd.notna(v) else None) for k, v in filt.items()},
            "taker_fills": int(vol[0] or 0),
            "taker_usdc": float(vol[1] or 0),
            "resolved_usdc": float(resolved or 0),
            "resolved_usdc_share": float((resolved or 0) / vol[1]) if vol[1] else None,
            "stage1": {
                "n_accounts": int(len(s1)),
                "n_eligible": int(len(e1)),
                "n_skilled_nominal": int(len(sk1_nom)),
                "n_skilled_fdr": int(len(sk1)),
                "eligible_skilled_share": _share(len(sk1_nom), len(e1)),
                "bh_q05": s1_gate.get("bh_q05_survivors"),
                "excess_z": s1_gate.get("excess_z"),
            },
            "stage2": {
                "n_accounts": int(len(s2)),
                "n_eligible": int(len(e2)),
                "n_skilled_nominal": int(len(sk2_nom)),
                "n_skilled_fdr": int(len(sk2)),
                "eligible_skilled_share": _share(len(sk2_nom), len(e2)),
                "bh_q05": s2_gate.get("bh_q05_survivors"),
                "excess_z": s2_gate.get("excess_z"),
                "gate_a_passed": s2_gate.get("passed"),
            },
            "survival": {
                "skilled_fdr_2024": int(len(s1_ids)),
                "appear_any_fill": int(len(appear)),
                "appear_share": _share(len(appear), len(s1_ids)),
                "classified_in_period": int(len(both_class)),
                "eligible_retest": int(len(retested)),
                "skilled_again_fdr": int(len(appear & set(sk2["user_id"].str.lower()))),
                "skilled_again_nominal": int(len(appear & set(sk2_nom["user_id"].str.lower()))),
                "mean_pnl_if_present": float(surv["total_pnl"].mean()) if len(surv) and "total_pnl" in surv else None,
                "sum_pnl_if_present": float(surv["total_pnl"].sum()) if len(surv) and "total_pnl" in surv else None,
                "nominal_skilled_appear": int(len(appear_nom)),
            },
            "watchlist": watch,
            "top_category": top_cat,
        }
        dbg.save_diagnostic_json("summary", summary)
        if trans is not None:
            dbg.save_diagnostic_df("transition_matrix", trans.reset_index())

        s2s = summary["stage2"]
        survs = summary["survival"]
        report = f"""# Stage 2 — recent period vs Oct–Nov 2024

**Period:** `{pid}`  
**Fill window:** {pd.to_datetime(vol[2], unit='s', utc=True)} → {pd.to_datetime(vol[3], unit='s', utc=True)}
({(int(vol[3]) - int(vol[2])) / 86400:.1f} days)

Fills are the 27 already-verified R2 slices
(2026-09-21 09:00Z to 2026-10-04 21:00Z). Combos (Exchange v3) and sports/esports/weather
markets are dropped in analysis (ingest leakage re-filtered).

## Universe

| | Value |
|---|---:|
| Raw slice rows | {int(filt.get('rows_raw') or 0):,} |
| Rows after combo/theme filter | {int(filt.get('rows_kept') or 0):,} |
| Excluded sports/weather markets | {int(filt.get('excluded_markets') or 0):,} |
| Taker fills / USDC (filtered) | {int(vol[0] or 0):,} / ${float(vol[1] or 0):,.0f} |
| USDC in markets resolved by cutoff | ${float(resolved or 0):,.0f} ({summary['resolved_usdc_share']:.1%}) |

A 13.5-day window is much shorter than Stage 1's 35 days, so fewer traders reach K≥5.
That is a power limit, not a pipeline failure.

## Apples-to-apples metrics (resolved-by-cutoff, eligible K≥5)

| Metric | Stage 1 (Oct–Nov 2024) | Stage 2 (Sep 21–Oct 4 2026) |
|---|---:|---:|
| Accounts in PnL table | {summary['stage1']['n_accounts']:,} | {s2s['n_accounts']:,} |
| Eligible (n_trades≥10, K≥5) | {summary['stage1']['n_eligible']:,} | {s2s['n_eligible']:,} |
| Nominal Skilled | {summary['stage1']['n_skilled_nominal']:,} ({summary['stage1']['eligible_skilled_share']:.2%}) | {s2s['n_skilled_nominal']:,} ({s2s['eligible_skilled_share']:.2%}) |
| BH q=0.05 survivors | {summary['stage1']['bh_q05']} | {s2s['bh_q05']} |
| Excess z vs discrete null | {summary['stage1']['excess_z']} | {s2s['excess_z']} |
| Gate A statistical | PASS | {'PASS' if s2s['gate_a_passed'] else 'FAIL'} |

## Wallet survival (2024 FDR Skilled → 2026)

Of **{survs['skilled_fdr_2024']:,}** Stage 1 FDR Skilled wallets:

| | N | Share of 2024 FDR Skilled |
|---|---:|---:|
| Any fill in this 13.5-day window | {survs['appear_any_fill']:,} | {survs['appear_share']:.1%} |
| Classified (not Excluded) | {survs['classified_in_period']:,} | {_share(survs['classified_in_period'], survs['skilled_fdr_2024']):.1%} |
| Retestable (K≥5, ≥10 trades) | {survs['eligible_retest']:,} | {_share(survs['eligible_retest'], survs['skilled_fdr_2024']):.1%} |
| Skilled again (nominal) | {survs['skilled_again_nominal']:,} | {_share(survs['skilled_again_nominal'], survs['skilled_fdr_2024']):.1%} |
| Skilled again (FDR) | {survs['skilled_again_fdr']:,} | {_share(survs['skilled_again_fdr'], survs['skilled_fdr_2024']):.1%} |

Sum of 2026 resolved-universe taker PnL for 2024 Skilled wallets that appear:
**${survs['sum_pnl_if_present'] if survs['sum_pnl_if_present'] is not None else float('nan'):,.0f}**
(mean ${survs['mean_pnl_if_present'] if survs['mean_pnl_if_present'] is not None else float('nan'):,.2f}).

This number governs copy-trading: if almost none of the 2024 Skilled set still trade,
following 2024 identities is not implementable, regardless of whether skill was real.

## Concentrated-winner watchlist

{json.dumps(watch, indent=2) if watch else 'No watchlist file on disk.'}

## Category mix (Stage 2)

{top_cat}

## Verdict

Either a clean negative (the 2024 label does not carry; almost nobody is still active)
or a thin positive (a handful of wallets remain Skilled) is evidence. A 13.5-day
non-election window is not a replication of Oct–Nov 2024; it is a different market.
"""
        out = Path("logs/periods") / "stage2_compare.md"
        out.write_text(report, encoding="utf-8")
        dbg.log("export", "report", str(out))
        (logs_path("stage2_compare") / "summary.json").write_text(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()

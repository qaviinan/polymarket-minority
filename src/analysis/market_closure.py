"""Stage 1.5: maker/taker settlement accounting and zero-sum closure."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis.event_grouping import register_event_views, resolved_by_cutoff_sql
from src.analysis.partition import register_order_flow_views
from src.lib.debug import debug_step
from src.lib.duck import get_duck
from src.lib.period import logs_path


def main() -> None:
    label_path = logs_path("coverage_audit", "account_classification_primary.parquet")
    if not label_path.exists():
        raise RuntimeError("Run coverage_audit first")

    with debug_step("market_closure") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        cutoff = int(con.execute("SELECT MAX(block_timestamp) FROM order_filled").fetchone()[0])
        con.execute(
            f"""
            CREATE OR REPLACE VIEW participant_fill_pnl AS
            SELECT lower(o.order_type) AS order_type, o.user_id, o.transaction_hash,
              o.counterparty_id, o.amount_usdc, COALESCE(o.fee,0) AS fee,
              (CASE WHEN upper(o.side)='BUY' THEN o.amount_shares ELSE -o.amount_shares END)
                * ot.resolved_payout
              - (CASE WHEN upper(o.side)='BUY' THEN o.amount_usdc ELSE -o.amount_usdc END)
              - COALESCE(o.fee,0) AS pnl
            FROM order_filled o
            JOIN outcome_tokens ot
              ON CAST(ot.token_id AS VARCHAR)=CAST(o.asset AS VARCHAR)
            JOIN markets m ON m.market_id=ot.market_id
            WHERE {resolved_by_cutoff_sql("m", str(cutoff))}
            """
        )
        null_pnl = con.execute(
            "SELECT COUNT(*) FROM participant_fill_pnl WHERE pnl IS NULL"
        ).fetchone()[0]
        dbg.log("closure", "null_pnl_fills", int(null_pnl))
        dbg.assertion(
            "payouts_complete",
            "green" if null_pnl == 0 else "red",
            "Every resolved-universe fill has a payout"
            if null_pnl == 0
            else f"{int(null_pnl)} fills have NULL pnl; payouts are incomplete",
        )
        totals = con.execute(
            """
            SELECT order_type, COUNT(*) AS fills, SUM(amount_usdc) AS volume,
              SUM(pnl) AS pnl, SUM(fee) AS fees
            FROM participant_fill_pnl GROUP BY 1 ORDER BY 1
            """
        ).df()
        dbg.save_diagnostic_df("maker_taker_pnl", totals)
        total_pnl = float(totals["pnl"].sum())
        total_fees = float(totals["fees"].sum())
        volume = float(totals["volume"].sum())
        pre_fee_leak = total_pnl + total_fees
        leak_bps = 10_000 * pre_fee_leak / max(volume, 1e-12)
        dbg.log("closure", "total_participant_pnl", total_pnl)
        dbg.log("closure", "total_fees", total_fees)
        dbg.log("closure", "pre_fee_leak", pre_fee_leak)
        dbg.log("closure", "pre_fee_leak_bps_volume", leak_bps)

        labels = pd.read_parquet(label_path)[["user_id", "trader_group"]]
        con.register("_labels", labels)
        makers = con.execute(
            """
            SELECT p.user_id, SUM(p.pnl) AS maker_pnl, SUM(p.amount_usdc) AS maker_volume,
              COUNT(*) AS maker_fills, l.trader_group AS taker_label
            FROM participant_fill_pnl p
            LEFT JOIN _labels l USING (user_id)
            WHERE p.order_type='maker'
            GROUP BY 1,5 ORDER BY maker_pnl DESC
            """
        ).df()
        dbg.save_diagnostic_df("maker_account_pnl", makers)
        skilled_overlap = int(
            makers.loc[makers["taker_label"] == "Skilled_Winner", "user_id"].nunique()
        )
        dbg.log("maker", "skilled_taker_wallets_with_maker_fills", skilled_overlap)

        pairing = con.execute(
            """
            WITH maker_rows AS (
              SELECT transaction_hash, counterparty_id, COUNT(*) AS n
              FROM participant_fill_pnl WHERE order_type='maker' GROUP BY 1,2
            ),
            takers AS (
              SELECT DISTINCT transaction_hash, user_id
              FROM participant_fill_pnl WHERE order_type='taker'
            )
            SELECT COUNT(*) AS maker_groups,
              COUNT(*) FILTER (WHERE t.user_id IS NOT NULL) AS counterparty_matches
            FROM maker_rows m
            LEFT JOIN takers t
              ON t.transaction_hash=m.transaction_hash AND t.user_id=m.counterparty_id
            """
        ).df()
        dbg.save_diagnostic_df("counterparty_pairing", pairing)
        pair_rate = float(
            pairing["counterparty_matches"].iloc[0]
            / max(pairing["maker_groups"].iloc[0], 1)
        )
        dbg.log("quality", "maker_counterparty_match_rate", pair_rate)
        closure_ok = abs(leak_bps) <= 1.0
        dbg.assertion(
            "zero_sum_closure",
            "green" if closure_ok else "red",
            f"Pre-fee accounting leak is {leak_bps:.3f} bps of participant volume",
        )
        dbg.assertion(
            "counterparty_semantics",
            "green" if pair_rate >= 0.95 else "red",
            f"{pair_rate:.1%} of maker transaction/counterparty groups map to a taker user",
        )
        con.unregister("_labels")


if __name__ == "__main__":
    main()

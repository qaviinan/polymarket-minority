"""Stage 1.5: category, event, and PnL concentration of the primary labels."""

from __future__ import annotations


import numpy as np
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

    with debug_step("composition_audit") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        labels = pd.read_parquet(label_path)[["user_id", "trader_group"]]
        con.register("_labels", labels)
        cutoff = int(con.execute("SELECT MAX(block_timestamp) FROM takers").fetchone()[0])
        con.execute(
            f"""
            CREATE OR REPLACE VIEW composition_fills AS
            SELECT t.user_id, t.amount_usdc, m.market_id, m.event_id,
              COALESCE(e.category,'Other') AS category, l.trader_group,
              (CASE WHEN upper(t.side)='BUY' THEN t.amount_shares ELSE -t.amount_shares END)
                * ot.resolved_payout
              - (CASE WHEN upper(t.side)='BUY' THEN t.amount_usdc ELSE -t.amount_usdc END)
              - COALESCE(t.fee,0) AS fill_pnl
            FROM takers t
            JOIN outcome_tokens ot
              ON CAST(ot.token_id AS VARCHAR)=CAST(t.asset AS VARCHAR)
            JOIN markets m ON m.market_id=ot.market_id
            LEFT JOIN events e ON e.event_id=m.event_id
            LEFT JOIN _labels l USING (user_id)
            WHERE {resolved_by_cutoff_sql("m", str(cutoff))}
            """
        )
        category = con.execute(
            """
            SELECT category, COALESCE(trader_group,'Absent') AS trader_group,
              COUNT(*) AS fills, COUNT(DISTINCT user_id) AS users,
              SUM(amount_usdc) AS volume, SUM(fill_pnl) AS pnl
            FROM composition_fills GROUP BY 1,2 ORDER BY volume DESC
            """
        ).df()
        dbg.save_diagnostic_df("category_by_group", category)

        event_pnl = con.execute(
            """
            SELECT user_id, event_id, SUM(fill_pnl) AS event_pnl
            FROM composition_fills
            WHERE trader_group='Skilled_Winner'
            GROUP BY 1,2
            """
        ).df()
        if len(event_pnl):
            event_pnl["abs_pnl"] = event_pnl["event_pnl"].abs()
            denom = event_pnl.groupby("user_id")["abs_pnl"].transform("sum").replace(0, np.nan)
            event_pnl["abs_share"] = event_pnl["abs_pnl"] / denom
            hhi = (
                event_pnl.assign(sq=event_pnl["abs_share"] ** 2)
                .groupby("user_id")
                .agg(
                    event_pnl_hhi=("sq", "sum"),
                    n_events=("event_id", "nunique"),
                    max_abs_event_share=("abs_share", "max"),
                )
                .reset_index()
            )
        else:
            hhi = pd.DataFrame(
                columns=["user_id", "event_pnl_hhi", "n_events", "max_abs_event_share"]
            )
        dbg.save_diagnostic_df("skilled_event_concentration", hhi)
        median_hhi = float(hhi["event_pnl_hhi"].median()) if len(hhi) else float("nan")
        median_max = (
            float(hhi["max_abs_event_share"].median()) if len(hhi) else float("nan")
        )
        dbg.log("concentration", "skilled_median_event_pnl_hhi", median_hhi)
        dbg.log("concentration", "skilled_median_max_event_share", median_max)

        totals = category.groupby("category", as_index=False)["volume"].sum()
        top = totals.nlargest(1, "volume")
        top_share = float(top["volume"].iloc[0] / totals["volume"].sum()) if len(top) else 0
        dbg.log("composition", "top_category", str(top["category"].iloc[0]) if len(top) else "")
        dbg.log("composition", "top_category_volume_share", top_share)
        dbg.assertion(
            "single_category_dominance",
            "yellow" if top_share > 0.50 else "green",
            f"Largest category contributes {top_share:.1%} of restricted-universe volume",
        )
        dbg.assertion(
            "skilled_event_concentration",
            "yellow" if median_max > 0.50 else "green",
            f"Median Skilled account's largest absolute-PnL event share is {median_max:.1%}",
        )
        con.unregister("_labels")


if __name__ == "__main__":
    main()

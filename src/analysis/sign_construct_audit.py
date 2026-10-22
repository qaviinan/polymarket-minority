"""Raw-fill negation, fee population, and counterfactual-size checks."""

from __future__ import annotations


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

    with debug_step("sign_construct_audit") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        labels = pd.read_parquet(label_path)[["user_id", "trader_group"]]
        con.register("_labels", labels)
        cutoff = int(con.execute("SELECT MAX(block_timestamp) FROM takers").fetchone()[0])
        con.execute(
            f"""
            CREATE OR REPLACE VIEW construct_fills AS
            SELECT t.*, m.market_id, m.event_id, m.volume AS market_volume,
              ot.resolved_payout, l.trader_group,
              CASE WHEN upper(t.side)='BUY' THEN t.amount_shares ELSE -t.amount_shares END
                AS signed_shares,
              CASE WHEN upper(t.side)='BUY' THEN t.amount_usdc ELSE -t.amount_usdc END
                AS signed_usdc
            FROM takers t
            JOIN outcome_tokens ot
              ON CAST(ot.token_id AS VARCHAR)=CAST(t.asset AS VARCHAR)
            JOIN markets m ON m.market_id=ot.market_id
            LEFT JOIN _labels l USING (user_id)
            WHERE {resolved_by_cutoff_sql("m", str(cutoff))}
            """
        )
        pairs = con.execute(
            """
            WITH pairs AS (
              SELECT user_id, event_id,
                SUM(signed_shares*resolved_payout-signed_usdc) AS event_gross,
                SUM((-signed_shares)*resolved_payout-(-signed_usdc)) AS flipped_gross,
                COUNT(*) AS fills
              FROM construct_fills
              GROUP BY 1,2
            )
            SELECT * FROM pairs USING SAMPLE 50 ROWS (reservoir, 42)
            """
        ).df()
        pairs["negation_error"] = pairs["flipped_gross"] + pairs["event_gross"]
        dbg.save_diagnostic_df("negation_sample", pairs)
        max_error = float(pairs["negation_error"].abs().max()) if len(pairs) else float("nan")
        dbg.log("construct", "max_abs_negation_error", max_error)

        fee = con.execute(
            """
            SELECT COUNT(*) AS fills,
              COUNT(*) FILTER (WHERE fee>0) AS positive_fee_fills,
              SUM(COALESCE(fee,0)) AS total_fees,
              SUM(amount_usdc) AS volume
            FROM construct_fills
            """
        ).df()
        fee["fee_fill_share"] = fee["positive_fee_fills"] / fee["fills"]
        fee["fee_bps"] = 10_000 * fee["total_fees"] / fee["volume"]
        dbg.save_diagnostic_df("fee_population", fee)

        ticket = con.execute(
            """
            SELECT trader_group,
              quantile_cont(amount_usdc/NULLIF(market_volume,0), 0.5) AS median_share,
              quantile_cont(amount_usdc/NULLIF(market_volume,0), 0.95) AS p95_share,
              quantile_cont(amount_usdc/NULLIF(market_volume,0), 0.99) AS p99_share,
              MAX(amount_usdc/NULLIF(market_volume,0)) AS max_share
            FROM construct_fills
            WHERE trader_group IS NOT NULL
            GROUP BY 1 ORDER BY 1
            """
        ).df()
        dbg.save_diagnostic_df("ticket_share_of_market_volume", ticket)
        dbg.assertion(
            "event_gross_negation",
            "green" if max_error <= 1e-7 else "red",
            f"Maximum raw-fill flip identity error is {max_error:.3g}",
        )
        dbg.assertion(
            "fee_materiality",
            "green" if float(fee["fee_bps"].iloc[0]) < 0.1 else "yellow",
            f"Observed fees are {float(fee['fee_bps'].iloc[0]):.4f} bps of volume",
        )
        con.unregister("_labels")


if __name__ == "__main__":
    main()

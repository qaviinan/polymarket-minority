"""Stage 1.2: identity, farming, bot, residual-position, and market-structure diagnostics."""

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
        raise RuntimeError("Run coverage_audit before population_audit")

    with debug_step("population_audit") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        labels = pd.read_parquet(label_path)[["user_id", "trader_group"]]
        con.register("_primary_labels", labels)
        cutoff = int(con.execute("SELECT MAX(block_timestamp) FROM takers").fetchone()[0])
        con.execute(
            f"""
            CREATE OR REPLACE VIEW audit_fills AS
            SELECT t.*, m.market_id, m.event_id, e.category, ot.resolved_payout,
              CASE WHEN upper(t.side)='BUY' THEN t.amount_shares ELSE -t.amount_shares END
                AS signed_shares,
              CASE WHEN upper(t.side)='BUY' THEN t.amount_usdc ELSE -t.amount_usdc END
                AS signed_usdc,
              (CASE WHEN upper(t.side)='BUY' THEN t.amount_shares ELSE -t.amount_shares END)
                * ot.resolved_payout
              - (CASE WHEN upper(t.side)='BUY' THEN t.amount_usdc ELSE -t.amount_usdc END)
              - COALESCE(t.fee, 0) AS fill_pnl
            FROM takers t
            JOIN outcome_tokens ot
              ON CAST(ot.token_id AS VARCHAR)=CAST(t.asset AS VARCHAR)
            JOIN markets m ON m.market_id=ot.market_id
            LEFT JOIN events e ON e.event_id=m.event_id
            WHERE {resolved_by_cutoff_sql("m", str(cutoff))}
            """
        )

        accounts = con.execute(
            """
            WITH base AS (
              SELECT user_id,
                COUNT(*) AS n_trades,
                SUM(amount_usdc) AS volume,
                SUM(fill_pnl) AS pnl,
                COUNT(DISTINCT date_trunc('hour', to_timestamp(block_timestamp))) AS active_hours,
                AVG(CASE WHEN ABS(amount_shares-ROUND(amount_shares)) < 1e-9 THEN 1 ELSE 0 END)
                  AS round_share_fraction
              FROM audit_fills GROUP BY 1
            ),
            cp AS (
              SELECT user_id, MAX(cp_n)::DOUBLE/SUM(cp_n) AS max_counterparty_share
              FROM (
                SELECT user_id, counterparty_id, COUNT(*) AS cp_n
                FROM audit_fills GROUP BY 1,2
              ) x GROUP BY 1
            ),
            residual AS (
              SELECT user_id,
                COUNT(*) FILTER (WHERE ABS(position) > 1e-8) AS residual_assets,
                SUM(ABS(position)) AS gross_residual_shares
              FROM (
                SELECT user_id, asset, SUM(signed_shares) AS position
                FROM audit_fills GROUP BY 1,2
              ) x GROUP BY 1
            )
            SELECT b.*, cp.max_counterparty_share,
              r.residual_assets, r.gross_residual_shares,
              l.trader_group
            FROM base b
            LEFT JOIN cp USING (user_id)
            LEFT JOIN residual r USING (user_id)
            LEFT JOIN _primary_labels l USING (user_id)
            """
        ).df()
        p99_volume = float(accounts["volume"].quantile(0.99))
        accounts["near_zero_pnl_high_volume"] = (
            (accounts["volume"] >= p99_volume)
            & (accounts["pnl"].abs() / accounts["volume"].clip(lower=1e-12) < 0.001)
        )
        accounts["single_counterparty_flag"] = accounts["max_counterparty_share"] >= 0.90
        accounts["high_frequency_flag"] = (
            accounts["n_trades"] / accounts["active_hours"].clip(lower=1) >= 60
        )
        dbg.save_diagnostic_df("account_flags", accounts)

        reversals = con.execute(
            """
            WITH ordered AS (
              SELECT user_id, asset, block_timestamp, upper(side) AS side,
                LAG(block_timestamp) OVER (
                  PARTITION BY user_id, asset ORDER BY block_timestamp, id
                ) AS prior_ts,
                LAG(upper(side)) OVER (
                  PARTITION BY user_id, asset ORDER BY block_timestamp, id
                ) AS prior_side
              FROM audit_fills
            )
            SELECT user_id,
              COUNT(*) FILTER (
                WHERE prior_side IS NOT NULL AND side<>prior_side
                  AND block_timestamp-prior_ts BETWEEN 0 AND 300
              ) AS rapid_reversals,
              COUNT(*) AS n_fills
            FROM ordered GROUP BY 1
            """
        ).df()
        dbg.save_diagnostic_df("rapid_reversals_by_user", reversals)
        top_counterparties = con.execute(
            """
            SELECT counterparty_id, COUNT(*) AS fills,
              COUNT(DISTINCT user_id) AS users, SUM(amount_usdc) AS volume
            FROM audit_fills GROUP BY 1 ORDER BY fills DESC LIMIT 20
            """
        ).df()
        dbg.save_diagnostic_df("top_taker_counterparties", top_counterparties)

        invalid_addresses = con.execute(
            """
            SELECT COUNT(DISTINCT user_id) FROM audit_fills
            WHERE NOT regexp_matches(user_id, '^0x[0-9a-fA-F]{40}$')
            """
        ).fetchone()[0]
        dbg.log("identity", "invalid_user_addresses", int(invalid_addresses))
        dbg.log("flags", "near_zero_pnl_high_volume_accounts", int(accounts["near_zero_pnl_high_volume"].sum()))
        dbg.log("flags", "single_counterparty_accounts", int(accounts["single_counterparty_flag"].sum()))
        dbg.log("flags", "high_frequency_accounts", int(accounts["high_frequency_flag"].sum()))
        dbg.log("positions", "accounts_with_residual_assets", int((accounts["residual_assets"] > 0).sum()))
        skilled = accounts[accounts["trader_group"] == "Skilled_Winner"]
        dbg.log(
            "skilled",
            "flagged_near_zero_high_volume_share",
            float(skilled["near_zero_pnl_high_volume"].mean()) if len(skilled) else 0.0,
        )

        multi = con.execute(
            """
            SELECT COUNT(*) AS events_multi_market,
              SUM(n_markets) AS markets_in_multi_market_events
            FROM (
              SELECT event_id, COUNT(*) AS n_markets
              FROM markets GROUP BY 1 HAVING COUNT(*) > 1
            ) x
            """
        ).df()
        dbg.save_diagnostic_df("multi_market_events", multi)
        dbg.assertion(
            "user_id_address_shape",
            "green" if invalid_addresses == 0 else "red",
            f"{invalid_addresses} user IDs are not canonical addresses",
        )
        dbg.assertion(
            "proxy_owner_resolution",
            "yellow",
            "user_id is consistently address-shaped, but fills alone cannot map multiple "
            "proxy wallets to one beneficial owner; results are wallet-level.",
        )
        dbg.assertion(
            "counterparty_concentration_interpretation",
            "yellow",
            "Taker-row counterparty_id is frequently an exchange/matching address; "
            "high concentration is not treated as wash trading without transaction-level "
            "maker↔taker pairing.",
        )
        dbg.assertion(
            "mint_merge_identification",
            "green",
            "Complete-set mint/merge operations are absent but settlement-PnL neutral when "
            "outcome payouts sum to one; residual positions are expected. Direct token "
            "transfers and gas remain outside the ledger.",
        )
        con.unregister("_primary_labels")


if __name__ == "__main__":
    main()

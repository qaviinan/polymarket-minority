"""Realized PnL for takers joined to outcome_tokens; roll up to account level."""

from __future__ import annotations

import os

from src.analysis.partition import minority_train_test_split_enabled, register_order_flow_views
from src.analysis.event_grouping import register_event_views, resolved_by_cutoff_sql
from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri


def register_pnl_views(con, *, taker_view: str | None = None) -> None:
    """Create pnl_user_asset and pnl_account views (requires order_filled + metadata views).

    If ``taker_view`` is set (e.g. ``takers_evt_train``), it overrides the default
    ``takers`` / ``takers_train`` choice from ``MINORITY_LABEL_SPLIT``.
    """
    if taker_view is not None:
        taker_src = taker_view
    else:
        taker_src = "takers_train" if minority_train_test_split_enabled() else "takers"
    universe = os.environ.get("PNL_MARKET_UNIVERSE", "all").strip().lower() or "all"
    if universe not in {"all", "resolved_by_fill_cutoff"}:
        raise RuntimeError("PNL_MARKET_UNIVERSE must be all or resolved_by_fill_cutoff")
    if universe == "resolved_by_fill_cutoff":
        taker_source_sql = f"""
          (
            SELECT t.*
            FROM {taker_src} t
            JOIN outcome_tokens ot0
              ON CAST(ot0.token_id AS VARCHAR) = CAST(t.asset AS VARCHAR)
            JOIN markets m0 ON m0.market_id = ot0.market_id
            WHERE {resolved_by_cutoff_sql(
                "m0", f"(SELECT MAX(CAST(block_timestamp AS BIGINT)) FROM {taker_src})"
            )}
          )
        """
    else:
        taker_source_sql = taker_src
    con.execute(
        f"""
        CREATE OR REPLACE VIEW pnl_user_asset AS
        WITH signed AS (
          SELECT
            user_id,
            CAST(asset AS VARCHAR) AS asset,
            CASE WHEN upper(side) = 'BUY' THEN amount_shares ELSE -amount_shares END AS s_shares,
            CASE WHEN upper(side) = 'BUY' THEN amount_usdc ELSE -amount_usdc END AS s_usdc,
            fee
          FROM {taker_source_sql}
        )
        SELECT
          s.user_id,
          s.asset,
          SUM(s.s_shares) * MAX(ot.resolved_payout) - SUM(s.s_usdc) - SUM(s.fee) AS pnl,
          SUM(s.s_shares) AS position_shares,
          SUM(s.s_usdc) AS cash_in,
          SUM(s.fee) AS fees,
          COUNT(*) AS n_trades
        FROM signed s
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = s.asset
        GROUP BY s.user_id, s.asset;
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW pnl_account AS
        SELECT
          p.user_id,
          SUM(p.pnl) AS total_pnl,
          SUM(p.n_trades) AS n_trades,
          COUNT(DISTINCT m.market_id) AS n_markets,
          COUNT(DISTINCT m.event_id) AS n_events
        FROM pnl_user_asset p
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = p.asset
        JOIN markets m ON m.market_id = ot.market_id
        GROUP BY p.user_id;
        """
    )


def main() -> None:
    with debug_step("pnl") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        register_pnl_views(con)
        dbg.log(
            "meta",
            "pnl_taker_source",
            "takers_train (first 3 week_index buckets)" if minority_train_test_split_enabled() else "takers (full window)",
        )
        universe = os.environ.get("PNL_MARKET_UNIVERSE", "all").strip().lower() or "all"
        dbg.log("meta", "pnl_market_universe", universe)

        acct = con.execute("SELECT * FROM pnl_account").df()
        dbg.log("ingest", "n_taker_accounts", len(acct))

        qs = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]
        pct = acct["total_pnl"].quantile(qs)
        pct_df = pct.reset_index()
        pct_df.columns = ["quantile", "value"]
        dbg.save_diagnostic_df("pnl_percentiles", pct_df)

        zero = con.execute("SELECT SUM(total_pnl) AS s FROM pnl_account").fetchone()[0] or 0
        dbg.log("quality", "sum_total_pnl_accounts", float(zero))

        top_w = con.execute(
            "SELECT * FROM pnl_account ORDER BY total_pnl DESC NULLS LAST LIMIT 20"
        ).df()
        top_l = con.execute(
            "SELECT * FROM pnl_account ORDER BY total_pnl ASC NULLS LAST LIMIT 20"
        ).df()
        dbg.save_diagnostic_df("top20_winners", top_w)
        dbg.save_diagnostic_df("top20_losers", top_l)

        hist = con.execute(
            """
            SELECT
              CASE
                WHEN n_trades < 2 THEN '1'
                WHEN n_trades < 5 THEN '2-4'
                WHEN n_trades < 10 THEN '5-9'
                WHEN n_trades < 20 THEN '10-19'
                WHEN n_trades < 50 THEN '20-49'
                WHEN n_trades < 100 THEN '50-99'
                WHEN n_trades < 500 THEN '100-499'
                ELSE '500+'
              END AS bucket,
              COUNT(*) AS c
            FROM pnl_account
            GROUP BY 1 ORDER BY 1
            """
        ).df()
        dbg.save_diagnostic_df("n_trades_histogram", hist)

        ge10 = con.execute("SELECT COUNT(*) FROM pnl_account WHERE n_trades >= 10").fetchone()[0]
        dbg.log("quality", "accounts_n_trades_ge_10", int(ge10))

        nonzero = con.execute(
            """
            SELECT COUNT(*) FROM pnl_user_asset WHERE ABS(position_shares) > 1e-8
            """
        ).fetchone()[0]
        dbg.log("quality", "user_asset_rows_nonzero_position", int(nonzero))
        if nonzero == 0:
            dbg.assertion("flat_positions", "green", "No residual open share positions at token level")
        else:
            dbg.assertion("flat_positions", "yellow", f"{nonzero} (user,asset) rows with non-zero net shares — expected small if fees/rounding")

        # Export to R2 for downstream steps
        label_scope = "train_weeks_0_2" if minority_train_test_split_enabled() else "all_window"
        uri = r2_uri("derived", "pnl_account.parquet")
        con.execute(
            f"COPY (SELECT *, '{label_scope}' AS label_scope, '{universe}' AS market_universe "
            "FROM pnl_account) "
            f"TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
        )
        dbg.log("export", "pnl_account_r2", uri)

        uri2 = r2_uri("derived", "pnl_user_asset.parquet")
        con.execute(
            f"COPY (SELECT *, '{label_scope}' AS label_scope, '{universe}' AS market_universe "
            "FROM pnl_user_asset) "
            f"TO '{uri2}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
        )
        dbg.log("export", "pnl_user_asset_r2", uri2)


if __name__ == "__main__":
    main()

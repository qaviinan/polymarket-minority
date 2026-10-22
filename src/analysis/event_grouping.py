"""Map market_id to event_id and YES token id using metadata Parquet on R2."""

from __future__ import annotations


import duckdb

from src.lib.duck import r2_uri
from src.lib.period import period_id


def register_event_views(con: duckdb.DuckDBPyConnection) -> None:
    """Register metadata tables as DuckDB views from R2 Parquet."""
    base = r2_uri("metadata")
    con.execute(
        f"""
        CREATE OR REPLACE VIEW events AS
        SELECT * FROM read_parquet('{base}/events.parquet');
        CREATE OR REPLACE VIEW markets AS
        SELECT * FROM read_parquet('{base}/markets.parquet');
        CREATE OR REPLACE VIEW outcome_tokens AS
        SELECT * FROM read_parquet('{base}/outcome_tokens.parquet');
        CREATE OR REPLACE VIEW market_resolution AS
        SELECT m.market_id,
          epoch(TRY_CAST(m.end_date AS TIMESTAMPTZ)) AS market_end_ts,
          epoch(TRY_CAST(e.end_date AS TIMESTAMPTZ)) AS event_end_ts,
          GREATEST(
            epoch(TRY_CAST(m.end_date AS TIMESTAMPTZ)),
            epoch(TRY_CAST(e.end_date AS TIMESTAMPTZ))
          ) AS scheduled_end_ts
        FROM markets m LEFT JOIN events e ON e.event_id = m.event_id;
        """
    )
    if period_id():
        apply_recent_universe_filters(con)


COMBO_EXCHANGE = "0xe3333700ca9d93003f00f0f71f8515005f6c00aa"
DROP_THEMES = {"Sports & esports", "Weather & science"}


def apply_recent_universe_filters(con: duckdb.DuckDBPyConnection) -> None:
    """Drop Exchange-v3 combos and sports/esports/weather fills for a PERIOD_ID run.

    Ingest already tries to exclude tagged tokens, but some get through, so analysis
    filters again from metadata. Combos have no Gamma catalog row.
    """
    import os

    from src.analysis.skilled_profile import classify_theme

    exclude_combos = os.environ.get("STAGE2_EXCLUDE_COMBOS", "1").strip().lower() not in {
        "0", "false", "no",
    }
    raw_themes = os.environ.get("STAGE2_EXCLUDE_THEMES", "Sports & esports,Weather & science")
    drop_themes = {t.strip() for t in raw_themes.split(",") if t.strip()} or set(DROP_THEMES)

    meta = con.execute(
        """
        SELECT m.market_id, e.category, e.title AS event_title, m.question
        FROM markets m LEFT JOIN events e ON e.event_id = m.event_id
        """
    ).df()
    if meta.empty:
        return
    meta["theme"] = [
        classify_theme(c, t, q)
        for c, t, q in zip(meta["category"], meta["event_title"], meta["question"])
    ]
    drop = meta.loc[meta["theme"].isin(drop_themes), ["market_id"]]
    con.execute("DROP TABLE IF EXISTS _excluded_markets")
    if len(drop):
        con.register("_drop_markets", drop)
        con.execute("CREATE TABLE _excluded_markets AS SELECT market_id FROM _drop_markets")
        con.unregister("_drop_markets")
    else:
        con.execute("CREATE TABLE _excluded_markets (market_id VARCHAR)")

    combo_pred = (
        f"AND lower(o.address) <> '{COMBO_EXCHANGE}'" if exclude_combos else ""
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW order_filled AS
        SELECT o.*
        FROM order_filled_src o
        WHERE CAST(o.asset AS VARCHAR) IN (
          SELECT CAST(ot.token_id AS VARCHAR) FROM outcome_tokens ot
          WHERE ot.market_id NOT IN (SELECT market_id FROM _excluded_markets)
        )
        {combo_pred}
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE _universe_filter_stats AS
        SELECT
          (SELECT COUNT(*) FROM order_filled_src) AS rows_raw,
          (SELECT COUNT(*) FROM order_filled) AS rows_kept,
          (SELECT COUNT(*) FROM _excluded_markets) AS excluded_markets
        """
    )


def resolved_by_cutoff_sql(market_alias: str, cutoff_sql: str) -> str:
    """Predicate: market and parent event end by the cutoff, and payouts are complete.

    Gamma market end dates can precede the real resolution (the Harris 2024 winner market
    lists 2024-11-04 12:00Z while its event ends 2024-11-05), so the event end is required too.
    A market cached before it closed has NULL ``resolved_payout``. Its fills stay out of the
    resolved universe, because NULL pnl becomes a NaN sign test and a false p of 0.
    """
    return (
        f"{market_alias}.market_id IN ("
        f"SELECT mr.market_id FROM market_resolution mr "
        f"JOIN ("
        f"  SELECT market_id FROM outcome_tokens "
        f"  GROUP BY market_id "
        f"  HAVING COUNT(*) = COUNT(resolved_payout) "
        f"     AND ABS(SUM(resolved_payout) - 1) < 0.01"
        f") paid ON paid.market_id = mr.market_id "
        f"WHERE mr.scheduled_end_ts <= {cutoff_sql})"
    )


def market_to_event_sql() -> str:
    """SQL fragment: join alias m must be markets table with market_id, event_id."""
    return "m.event_id"


def yes_token_column() -> str:
    """Name of column on markets table for YES outcome token id."""
    return "yes_token_id"

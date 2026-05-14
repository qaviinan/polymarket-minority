"""Map market_id → event_id and YES token id using metadata Parquet on R2."""

from __future__ import annotations

from pathlib import Path

import duckdb

from src.lib.duck import get_duck, r2_uri


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
        """
    )


def market_to_event_sql() -> str:
    """SQL fragment: join alias m must be markets table with market_id, event_id."""
    return "m.event_id"


def yes_token_column() -> str:
    """Name of column on markets table for YES outcome token id."""
    return "yes_token_id"

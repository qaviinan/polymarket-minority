"""Register DuckDB views for order_filled, takers, and makers on R2 Parquet."""

from __future__ import annotations

import os

import duckdb

from src.lib.debug import debug_step
from src.lib.duck import r2_uri

MINORITY_SPLIT_ENV = "train_3w_test_1w"
_SECONDS_PER_DAY = 86400
_SECONDS_PER_WEEK = 604800


def week_index_for_ts(ts: int, anchor_ts: int) -> int:
    """Week bucket for a timestamp, same formula as the SQL views (floored at 0, no upper cap)."""
    return max(0, (int(ts) - int(anchor_ts)) // _SECONDS_PER_WEEK)


def minority_train_test_split_enabled() -> bool:
    return os.environ.get("MINORITY_LABEL_SPLIT", "").strip().lower() == MINORITY_SPLIT_ENV


def _parse_ts_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return int(raw)


def _resolve_analysis_ts_bounds(
    con: duckdb.DuckDBPyConnection, pattern: str
) -> tuple[int | None, int | None]:
    """Return (ts_min, ts_max) inclusive Unix seconds, or (None, None) for full history."""
    mn = _parse_ts_env("ANALYSIS_BLOCK_TS_MIN")
    mx = _parse_ts_env("ANALYSIS_BLOCK_TS_MAX")
    last_n = os.environ.get("ANALYSIS_USE_LAST_N_DAYS", "").strip()
    if last_n:
        if mn is not None or mx is not None:
            raise RuntimeError(
                "Use either ANALYSIS_BLOCK_TS_MIN/MAX together or ANALYSIS_USE_LAST_N_DAYS, not both."
            )
        n = int(last_n)
        if n <= 0:
            raise RuntimeError("ANALYSIS_USE_LAST_N_DAYS must be a positive integer")
        row = con.execute(
            f"""
            SELECT MAX(CAST(block_timestamp AS BIGINT))
            FROM read_parquet('{pattern}', hive_partitioning = true)
            """
        ).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("No rows in order_filled Parquet; cannot apply ANALYSIS_USE_LAST_N_DAYS")
        hi = int(row[0])
        lo = hi - n * _SECONDS_PER_DAY
        return lo, hi

    if (mn is None) ^ (mx is None):
        raise RuntimeError("Set both ANALYSIS_BLOCK_TS_MIN and ANALYSIS_BLOCK_TS_MAX, or neither.")
    if mn is not None and mx is not None and mn > mx:
        raise RuntimeError("ANALYSIS_BLOCK_TS_MIN must be <= ANALYSIS_BLOCK_TS_MAX")
    return mn, mx


def _resolve_week_anchor_ts(
    con: duckdb.DuckDBPyConnection, pattern: str, ts_min: int | None, ts_max: int | None
) -> int | None:
    """Epoch seconds for week_index=0 start; None if week_index is not computed."""
    if ts_min is None or ts_max is None:
        return None
    anchor_s = os.environ.get("ANALYSIS_WEEK_ANCHOR_TS", "").strip()
    if anchor_s:
        return int(anchor_s)
    return ts_min


def register_order_flow_views(con: duckdb.DuckDBPyConnection) -> None:
    pattern = r2_uri("order_filled", "**", "*.parquet")
    ts_min, ts_max = _resolve_analysis_ts_bounds(con, pattern)

    if minority_train_test_split_enabled() and ts_min is None:
        raise RuntimeError(
            f"MINORITY_LABEL_SPLIT={MINORITY_SPLIT_ENV} requires a bounded window: set "
            "ANALYSIS_BLOCK_TS_MIN and ANALYSIS_BLOCK_TS_MAX, or ANALYSIS_USE_LAST_N_DAYS."
        )

    where = ""
    if ts_min is not None:
        where = f"WHERE CAST(block_timestamp AS BIGINT) BETWEEN {ts_min} AND {ts_max}"

    week_anchor = _resolve_week_anchor_ts(con, pattern, ts_min, ts_max)
    if week_anchor is not None:
        week_expr = (
            f"GREATEST(0, FLOOR((CAST(x.block_timestamp AS BIGINT) - {week_anchor}) "
            f"/ {_SECONDS_PER_WEEK}.0)::INTEGER)"
        )
    else:
        week_expr = "0"

    con.execute(
        f"""
        CREATE OR REPLACE VIEW order_filled_src AS
        SELECT x.*, {week_expr} AS week_index
        FROM (
          SELECT * FROM read_parquet('{pattern}', hive_partitioning = true)
          {where}
        ) AS x;

        CREATE OR REPLACE VIEW order_filled AS
        SELECT * FROM order_filled_src;

        CREATE OR REPLACE VIEW takers AS
        SELECT * FROM order_filled WHERE lower(order_type) = 'taker';

        CREATE OR REPLACE VIEW takers_train AS
        SELECT * FROM takers WHERE week_index < 3;

        CREATE OR REPLACE VIEW takers_test AS
        SELECT * FROM takers WHERE week_index = 3;

        CREATE OR REPLACE VIEW takers_overflow AS
        SELECT * FROM takers WHERE week_index > 3;

        CREATE OR REPLACE VIEW makers AS
        SELECT * FROM order_filled WHERE lower(order_type) = 'maker';
        """
    )


def main() -> None:
    from src.lib.duck import get_duck

    with debug_step("partition") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        tot = con.execute("SELECT COUNT(*) FROM order_filled").fetchone()[0]
        tk = con.execute("SELECT COUNT(*) FROM takers").fetchone()[0]
        tt = con.execute("SELECT COUNT(*) FROM takers_train").fetchone()[0]
        mk = con.execute("SELECT COUNT(*) FROM makers").fetchone()[0]
        dbg.log("counts", "order_filled_rows", int(tot))
        dbg.log("counts", "taker_rows", int(tk))
        dbg.log("counts", "takers_train_rows", int(tt))
        dbg.log("counts", "maker_rows", int(mk))

        ts_bounds = con.execute(
            "SELECT MIN(CAST(block_timestamp AS BIGINT)), MAX(CAST(block_timestamp AS BIGINT)) FROM order_filled"
        ).fetchone()
        if ts_bounds and ts_bounds[0] is not None:
            dbg.log("ingest", "block_timestamp_min", int(ts_bounds[0]))
            dbg.log("ingest", "block_timestamp_max", int(ts_bounds[1]))

        wk = con.execute(
            """
            SELECT week_index, COUNT(*) AS c FROM takers GROUP BY 1 ORDER BY 1
            """
        ).df()
        dbg.save_diagnostic_df("takers_by_week_index", wk)
        test_stats = con.execute(
            """
            SELECT COUNT(DISTINCT CAST(to_timestamp(block_timestamp) AS DATE)),
                   MIN(block_timestamp), MAX(block_timestamp)
            FROM takers_test
            """
        ).fetchone()
        test_days = int(test_stats[0])
        test_span_s = (
            int(test_stats[2]) - int(test_stats[1])
            if test_stats[1] is not None and test_stats[2] is not None
            else 0
        )
        overflow = con.execute("SELECT COUNT(*) FROM takers_overflow").fetchone()[0]
        dbg.log("quality", "test_distinct_calendar_days", test_days)
        dbg.log("quality", "test_observed_span_seconds", test_span_s)
        dbg.log("quality", "taker_rows_after_test_week", int(overflow))
        if minority_train_test_split_enabled() and test_span_s >= _SECONDS_PER_WEEK:
            dbg.assertion(
                "exact_test_week",
                "red",
                f"Test fills span {test_span_s}s, expected less than one exact week",
            )
        elif minority_train_test_split_enabled():
            dbg.assertion(
                "exact_test_week",
                "green",
                f"Test bucket is bounded to one week; {overflow} later fills are explicit overflow",
            )

        u_tot = con.execute("SELECT COUNT(DISTINCT user_id) FROM order_filled").fetchone()[0]
        u_tk = con.execute("SELECT COUNT(DISTINCT user_id) FROM takers").fetchone()[0]
        u_mk = con.execute("SELECT COUNT(DISTINCT user_id) FROM makers").fetchone()[0]
        dbg.log("counts", "distinct_users_all", int(u_tot))
        dbg.log("counts", "distinct_users_takers", int(u_tk))
        dbg.log("counts", "distinct_users_makers", int(u_mk))

        overlap = con.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT user_id FROM takers INTERSECT SELECT user_id FROM makers
            ) x
            """
        ).fetchone()[0]
        dbg.log("quality", "users_in_both_taker_and_maker", int(overlap))
        makers_list = con.execute("SELECT DISTINCT user_id FROM makers").df()
        dbg.save_diagnostic_df("makers_user_list", makers_list)

        if overlap == 0:
            dbg.assertion("overlap", "green", "No user overlap between takers and makers views (unexpected if many MM)")
        else:
            dbg.assertion("overlap", "yellow", f"{overlap} users appear as both maker and taker (expected for some flow)")


if __name__ == "__main__":
    main()

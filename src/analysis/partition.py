"""Register DuckDB views for order_filled, takers, and makers on R2 Parquet."""

from __future__ import annotations

import duckdb

from src.lib.debug import debug_step
from src.lib.duck import r2_uri


def register_order_flow_views(con: duckdb.DuckDBPyConnection) -> None:
    pattern = r2_uri("order_filled", "**", "*.parquet")
    con.execute(
        f"""
        CREATE OR REPLACE VIEW order_filled AS
        SELECT * FROM read_parquet('{pattern}', hive_partitioning = true);

        CREATE OR REPLACE VIEW takers AS
        SELECT * FROM order_filled WHERE lower(order_type) = 'taker';

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
        mk = con.execute("SELECT COUNT(*) FROM makers").fetchone()[0]
        dbg.log("counts", "order_filled_rows", int(tot))
        dbg.log("counts", "taker_rows", int(tk))
        dbg.log("counts", "maker_rows", int(mk))

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

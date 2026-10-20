"""Post-ingest checks: row counts, volume vs Gamma, maker/taker, top users."""

from __future__ import annotations

import os


from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri


def main() -> None:
    with debug_step("verify_ingest") as dbg:
        con = get_duck()
        pattern = r2_uri("order_filled", "**", "*.parquet")
        meta_m = r2_uri("metadata", "markets.parquet")
        meta_t = r2_uri("metadata", "outcome_tokens.parquet")

        try:
            totals = con.execute(
                f"""
                SELECT
                  COUNT(*) AS n_rows,
                  SUM(amount_usdc) AS sum_usdc,
                  MIN(block_timestamp) AS ts_min,
                  MAX(block_timestamp) AS ts_max
                FROM read_parquet('{pattern}', hive_partitioning = true)
                """
            ).df()
        except Exception as e:
            dbg.assertion("read_parquet", "red", f"Cannot read order_filled: {e}")
            raise

        dbg.log("ingest", "n_rows", int(totals["n_rows"].iloc[0]))
        dbg.log("ingest", "sum_usdc", float(totals["sum_usdc"].iloc[0] or 0))
        dbg.log("ingest", "ts_min", int(totals["ts_min"].iloc[0]))
        dbg.log("ingest", "ts_max", int(totals["ts_max"].iloc[0]))

        daily = con.execute(
            f"""
            SELECT CAST(block_timestamp AS BIGINT) / 86400 * 86400 AS day_ts, COUNT(*) AS c
            FROM read_parquet('{pattern}', hive_partitioning = true)
            GROUP BY 1 ORDER BY 1
            """
        ).df()
        dbg.save_diagnostic_df("daily_row_counts", daily)
        day_values = sorted(int(x) for x in daily["day_ts"].tolist())
        missing_day_gaps = [
            (a, b) for a, b in zip(day_values, day_values[1:]) if b - a > 86400
        ]
        dbg.log("quality", "missing_calendar_day_gaps", len(missing_day_gaps))
        dbg.assertion(
            "daily_continuity",
            "green" if not missing_day_gaps else "red",
            "No missing calendar-day partitions"
            if not missing_day_gaps
            else f"{len(missing_day_gaps)} gaps exceed one day",
        )

        expected_max = os.environ.get("EXPECTED_FILL_TS_MAX", "").strip()
        accepted_truncation = os.environ.get("ACCEPT_TRUNCATED_INGEST", "").lower() in {
            "1", "true", "yes"
        }
        if expected_max:
            actual_max = int(totals["ts_max"].iloc[0])
            complete = actual_max >= int(expected_max)
            dbg.assertion(
                "expected_fill_horizon",
                "green" if complete else ("yellow" if accepted_truncation else "red"),
                f"actual ts_max={actual_max}, expected >= {expected_max}; "
                f"accepted_truncation={accepted_truncation}",
            )

        mt = con.execute(
            f"""
            SELECT order_type, COUNT(*) AS c, SUM(amount_usdc) AS usdc
            FROM read_parquet('{pattern}', hive_partitioning = true)
            GROUP BY 1
            """
        ).df()
        dbg.save_diagnostic_df("maker_taker_split", mt)

        bs = con.execute(
            f"""
            SELECT side, COUNT(*) AS c FROM read_parquet('{pattern}', hive_partitioning = true)
            GROUP BY 1
            """
        ).df()
        dbg.save_diagnostic_df("buy_sell_split", bs)

        vol_cmp = con.execute(
            f"""
            WITH trades AS (
              SELECT asset, SUM(amount_usdc) AS usdc_onchain
              FROM read_parquet('{pattern}', hive_partitioning = true)
              GROUP BY 1
            ),
            tok AS (
              SELECT token_id, market_id FROM read_parquet('{meta_t}')
            ),
            m AS (
              SELECT market_id, volume AS gamma_volume FROM read_parquet('{meta_m}')
            )
            SELECT
              m.market_id,
              m.gamma_volume,
              SUM(t.usdc_onchain) AS usdc_onchain
            FROM trades t
            JOIN tok ON tok.token_id = CAST(t.asset AS VARCHAR)
            JOIN m USING (market_id)
            GROUP BY m.market_id, m.gamma_volume
            """
        ).df()
        vol_cmp["ratio"] = vol_cmp["usdc_onchain"] / vol_cmp["gamma_volume"].replace(0, float("nan"))
        dbg.save_diagnostic_df("volume_gamma_vs_onchain", vol_cmp)
        bad = vol_cmp[vol_cmp["ratio"].notna() & ((vol_cmp["ratio"] > 1.25) | (vol_cmp["ratio"] < 0.75))]
        dbg.log("quality", "markets_volume_mismatch_gt_25pct", len(bad))
        if len(bad) > 0:
            dbg.save_diagnostic_df("volume_mismatch_markets", bad.head(50))
            dbg.assertion("volume_alignment", "yellow", f"{len(bad)} markets with >25% volume mismatch vs Gamma")
        else:
            dbg.assertion("volume_alignment", "green", "No large volume mismatches")

        top_n = con.execute(
            f"""
            SELECT user_id, COUNT(*) AS n_trades, SUM(amount_usdc) AS usdc
            FROM read_parquet('{pattern}', hive_partitioning = true)
            GROUP BY 1 ORDER BY n_trades DESC LIMIT 10
            """
        ).df()
        dbg.save_diagnostic_df("top10_users_by_trades", top_n)

        top_v = con.execute(
            f"""
            SELECT user_id, COUNT(*) AS n_trades, SUM(amount_usdc) AS usdc
            FROM read_parquet('{pattern}', hive_partitioning = true)
            GROUP BY 1 ORDER BY usdc DESC NULLS LAST LIMIT 10
            """
        ).df()
        dbg.save_diagnostic_df("top10_users_by_usdc", top_v)

        n_assets = con.execute(
            f"SELECT COUNT(DISTINCT asset) AS c FROM read_parquet('{pattern}', hive_partitioning = true)"
        ).fetchone()[0]
        n_meta = con.execute(f"SELECT COUNT(*) AS c FROM read_parquet('{meta_t}')").fetchone()[0]
        dbg.log("quality", "distinct_trade_assets", int(n_assets))
        dbg.log("quality", "metadata_outcome_tokens", int(n_meta))

        from src.lib.period import period_id

        combo = "0xe3333700ca9d93003f00f0f71f8515005f6c00aa"
        combo_pred = (
            f"AND lower(o.address) <> '{combo}'" if period_id() else ""
        )
        orphan = con.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM read_parquet('{pattern}', hive_partitioning = true) o
            LEFT JOIN read_parquet('{meta_t}') t ON CAST(t.token_id AS VARCHAR) = CAST(o.asset AS VARCHAR)
            WHERE t.token_id IS NULL
            {combo_pred}
            """
        ).fetchone()[0]
        dbg.log("quality", "rows_with_asset_not_in_metadata", int(orphan))
        if orphan > 0:
            level = "yellow" if period_id() else "red"
            dbg.assertion(
                "metadata_join",
                level,
                f"{orphan} fills reference assets outside metadata universe"
                + (" (non-combo; analysis inner-joins tokens)" if period_id() else ""),
            )
        else:
            dbg.assertion("metadata_join", "green", "All assets join to outcome_tokens")

        duplicates = con.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT id FROM read_parquet('{pattern}', hive_partitioning=true)
              GROUP BY 1 HAVING COUNT(*) > 1
            ) x
            """
        ).fetchone()[0]
        dbg.log("quality", "duplicate_fill_ids", int(duplicates))
        dbg.assertion(
            "unique_fill_ids",
            "green" if duplicates == 0 else "red",
            f"{duplicates} duplicate fill IDs",
        )


if __name__ == "__main__":
    main()

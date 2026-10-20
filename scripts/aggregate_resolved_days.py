"""Aggregate resolved fills one calendar day at a time.

A single scan of the whole tape holds an R2 signature long enough for Cloudflare
to reject it with RequestTimeTooSkewed. Each day opens a fresh connection and
writes data/periods/recent_2026/analysis/user_event_days/YYYY-MM-DD.parquet.
Rerun the script to resume: a non-empty day file is skipped.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

from src.lib.duck import get_duck, load_env, r2_uri

OUT = Path("data/periods/recent_2026/analysis/user_event_days")
PAYOUTS = "data/periods/recent_2026/analysis/payouts.parquet"
COMBO = "0xe3333700ca9d93003f00f0f71f8515005f6c00aa"


def _days() -> list[date]:
    d0 = date.fromisoformat(os.environ.get("AGG_START", "2026-08-10"))
    d1 = date.fromisoformat(os.environ.get("AGG_END", "2026-10-04"))
    out = []
    d = d0
    while d <= d1:
        out.append(d)
        d += timedelta(days=1)
    return out


def _one_day(day: date) -> None:
    out = OUT / f"{day.isoformat()}.parquet"
    marker = OUT / f"{day.isoformat()}.empty"
    if marker.exists() or (out.exists() and out.stat().st_size > 0):
        print(f"skip {day}", flush=True)
        return
    load_env()
    con = get_duck()
    con.execute("SET memory_limit='3GB'")
    con.execute("SET preserve_insertion_order=false")
    src = r2_uri("order_filled", "**", f"dt={day.isoformat()}", "*.parquet")
    print(f"day {day}", flush=True)
    try:
        _copy(con, src, out)
    except Exception as exc:
        msg = str(exc)
        if "No files found" in msg:
            print(f"empty {day}", flush=True)
            marker.write_text("empty\n", encoding="utf-8")
            return
        if out.exists():
            out.unlink()
        if "RequestTimeTooSkewed" not in msg and "403" not in msg:
            raise
        print(f"retry {day} after clock skew", flush=True)
        time.sleep(20)
        con = get_duck()
        con.execute("SET memory_limit='3GB'")
        con.execute("SET preserve_insertion_order=false")
        try:
            _copy(con, src, out)
        except Exception:
            if out.exists() and out.stat().st_size == 0:
                out.unlink()
            raise
    print(f"wrote {day} {out.stat().st_size} bytes", flush=True)


def _copy(con, src: str, out: Path) -> None:
    con.execute(
        f"""
        COPY (
          SELECT
            f.user_id,
            p.event_id,
            f.order_type,
            SUM(
              (CASE WHEN upper(f.side)='BUY' THEN f.amount_shares ELSE -f.amount_shares END) * p.resolved_payout
              - (CASE WHEN upper(f.side)='BUY' THEN f.amount_usdc ELSE -f.amount_usdc END)
              - f.fee
            ) AS pnl,
            SUM(f.fee) AS fees,
            SUM(f.amount_usdc) AS volume,
            COUNT(*) AS n_trades,
            MIN(CAST(f.block_timestamp AS BIGINT)) AS min_ts,
            MAX(CAST(f.block_timestamp AS BIGINT)) AS max_ts
          FROM read_parquet('{src}', hive_partitioning=true) f
          JOIN read_parquet('{PAYOUTS}') p ON p.token_id = CAST(f.asset AS VARCHAR)
          WHERE lower(f.address) <> '{COMBO}'
          GROUP BY f.user_id, p.event_id, f.order_type
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def main() -> None:
    os.environ["PERIOD_ID"] = os.environ.get("PERIOD_ID", "recent_2026")
    OUT.mkdir(parents=True, exist_ok=True)
    for day in _days():
        _one_day(day)
    print("days complete", flush=True)


if __name__ == "__main__":
    main()

"""Copy the active PERIOD_ID's R2 fills + metadata to a local mirror (no Goldsky)."""

from __future__ import annotations

import os
from pathlib import Path

from src.lib.duck import get_duck, load_env, r2_uri
from src.lib.period import local_dir, period_id


def main() -> None:
    load_env()
    pid = period_id()
    if not pid:
        raise RuntimeError("Set PERIOD_ID")
    # Force remote reads even if LOCAL_R2_MIRROR is already exported.
    os.environ.pop("LOCAL_R2_MIRROR", None)
    dest = Path(os.environ.get("LOCAL_R2_MIRROR_DEST", str(local_dir("r2_mirror"))))
    fills_dir = dest / "order_filled"
    meta_dir = dest / "metadata"
    fills_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    con = get_duck()
    # Metadata first: the fill copy keeps only tokens in markets that ended by the
    # cutoff with a complete payout. The full tape does not fit on this disk.
    for name in ("events", "markets", "outcome_tokens"):
        uri = r2_uri("metadata", f"{name}.parquet")
        out = meta_dir / f"{name}.parquet"
        print(f"COPY {uri} -> {out}")
        con.execute(
            f"COPY (SELECT * FROM read_parquet('{uri}')) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    fills_out = fills_dir / "all.parquet"
    src = r2_uri("order_filled", "**", "*.parquet")
    if fills_out.exists() and os.environ.get("REMATERIALIZE", "").strip() not in {"1", "true"}:
        print(f"reuse {fills_out} ({fills_out.stat().st_size} bytes)")
    else:
        cutoff = os.environ.get("FILL_CUTOFF_TS", "1791147600")
        print(f"COPY resolved fills {src} -> {fills_out} cutoff={cutoff}")
        con.execute(
            f"""
            COPY (
              WITH resolution AS (
                SELECT m.market_id,
                  GREATEST(epoch(TRY_CAST(m.end_date AS TIMESTAMPTZ)),
                           epoch(TRY_CAST(e.end_date AS TIMESTAMPTZ))) AS scheduled_end_ts
                FROM read_parquet('{meta_dir / "markets.parquet"}') m
                LEFT JOIN read_parquet('{meta_dir / "events.parquet"}') e USING (event_id)
              ),
              paid AS (
                SELECT market_id FROM read_parquet('{meta_dir / "outcome_tokens.parquet"}')
                GROUP BY 1
                HAVING COUNT(*) = COUNT(resolved_payout)
                   AND ABS(SUM(resolved_payout) - 1) < 0.01
              ),
              tokens AS (
                SELECT CAST(token_id AS VARCHAR) AS asset
                FROM read_parquet('{meta_dir / "outcome_tokens.parquet"}')
                WHERE market_id IN (
                  SELECT r.market_id FROM resolution r
                  JOIN paid USING (market_id)
                  WHERE r.scheduled_end_ts <= {cutoff}
                )
              )
              SELECT id, block_number, block_timestamp, transaction_hash, address,
                     user_id, asset, amount_usdc, amount_shares, price, side,
                     counterparty_id, order_type, fee
              FROM read_parquet('{src}', hive_partitioning=true)
              WHERE CAST(asset AS VARCHAR) IN (SELECT asset FROM tokens)
            ) TO '{fills_out}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        print(f"wrote {fills_out} ({fills_out.stat().st_size} bytes)")
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fills_out}')").fetchone()[0]
    ts = con.execute(
        f"SELECT MIN(CAST(block_timestamp AS BIGINT)), MAX(CAST(block_timestamp AS BIGINT)) "
        f"FROM read_parquet('{fills_out}')"
    ).fetchone()
    print(f"fills rows={n:,} ts=[{ts[0]}, {ts[1]}]")
    print("LOCAL_R2_MIRROR", dest)


if __name__ == "__main__":
    main()

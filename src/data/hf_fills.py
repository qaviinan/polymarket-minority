"""Stream HuggingFace ``orderfilled.parquet`` into R2 in the existing fill schema.

Reads one row group at a time (column-pruned, so ``order_hash`` is never downloaded),
drops <=15-minute crypto Up/Down markets, and writes ZSTD parquet under
``periods/<PERIOD_ID>/order_filled/dt=YYYY-MM-DD/hf_rgNNNNN.parquet``. Nothing is
written to local disk except a small resume manifest.

2026 HF timestamps match the chain. Earlier snapshots extrapolate them (off by
minutes to hours); when a spot check disagrees with Polygon by more than 2 seconds,
timestamps are rebuilt from block anchors every 1,000 blocks.

    PERIOD_ID=recent_2026 python -m src.data.hf_fills
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.lib.checkpoint import Manifest
from src.lib.duck import get_duck, load_env, r2_bucket, r2_uri
from src.lib.period import period_id
from src.lib.polygon import block_timestamp, default_search_bounds, find_block_at_or_before

HF_ORDERFILLED = (
    "https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data/resolve/main/orderfilled.parquet"
)
HF_MARKETS = (
    "https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data/resolve/main/markets.parquet"
)
EXCHANGE_ADDRESS = {
    "CTF_EXCHANGE": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
    "NEGRISK_CTF_EXCHANGE": "0xc5d563a36ae78145c45a50134d48a1215220f80a",
    "CTF_EXCHANGE_V2": "0xe111180000d2663c0091e4f400237545b87b996b",
    "NEGRISK_CTF_EXCHANGE_V2": "0xe2222d279d744050d28e00520010520000310f59",
}
EXCHANGE_ADDRESSES = set(EXCHANGE_ADDRESS.values())
READ_COLUMNS = [
    "timestamp",
    "block_number",
    "transaction_hash",
    "log_index",
    "contract",
    "maker",
    "taker",
    "maker_asset_id",
    "taker_asset_id",
    "maker_amount_filled",
    "taker_amount_filled",
    "maker_fee",
    "taker_fee",
    "protocol_fee",
]
FILL_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("block_number", pa.int64()),
        ("block_timestamp", pa.int64()),
        ("transaction_hash", pa.string()),
        ("address", pa.string()),
        ("user_id", pa.string()),
        ("asset", pa.string()),
        ("amount_usdc", pa.float64()),
        ("amount_shares", pa.float64()),
        ("price", pa.float64()),
        ("tx_type", pa.string()),
        ("side", pa.string()),
        ("order_hash", pa.string()),
        ("counterparty_id", pa.string()),
        ("order_type", pa.string()),
        ("fee", pa.float64()),
        ("builder", pa.string()),
        ("dt", pa.date32()),
        ("_gs_op", pa.string()),
    ]
)
_SHORT_SLUG = re.compile(r"(?:^|-)(?:5m|15m)(?:-|$)")
_ANCHOR_EVERY = 1000
_TS_TOLERANCE_S = 2


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}Z] {msg}", file=sys.stderr, flush=True)


def is_short_crypto_slug(slug: str | None) -> bool:
    """True for 5-minute and 15-minute crypto Up/Down markets, not hourly or daily ones."""
    s = (slug or "").lower()
    if any(p in s for p in ("updown-5m", "updown-15m", "up-or-down-5m", "up-or-down-15m")):
        return True
    return _SHORT_SLUG.search(s) is not None


def row_groups_overlapping(stats: list[tuple[int, int, int]], lo: int, hi: int) -> list[int]:
    """Row groups whose inclusive block range intersects ``[lo, hi)``."""
    return [rg for rg, mn, mx in stats if mx >= lo and mn < hi]


def interpolate_timestamps(blocks: np.ndarray, anchors: np.ndarray, anchor_ts: np.ndarray) -> np.ndarray:
    """Linear block→timestamp map. Anchors must be sorted and cover ``blocks``."""
    return np.rint(np.interp(blocks.astype(np.float64), anchors.astype(np.float64), anchor_ts.astype(np.float64))).astype(
        np.int64
    )


def u256_to_float(arr: pa.Array) -> np.ndarray:
    """Little-endian uint256 fixed-binary column, divided by 1e6. Values above 2^53 lose cents."""
    if isinstance(arr, pa.ChunkedArray):
        return np.concatenate([u256_to_float(chunk) for chunk in arr.chunks]) if len(arr) else np.zeros(0)
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    if n == 0:
        return out
    buf = arr.buffers()[1]
    raw = np.frombuffer(buf, dtype=np.uint8, count=n * 32).reshape(n, 32)
    scale = 1.0
    for k in range(12):
        out += raw[:, k] * scale
        scale *= 256.0
    out /= 1e6
    if arr.null_count:
        out[np.asarray(arr.is_null())] = 0.0
    return out


def _const_string(value: str, n: int) -> pa.Array:
    return pa.DictionaryArray.from_arrays(np.zeros(n, dtype=np.int32), pa.array([value], type=pa.string())).cast(
        pa.string()
    )


def fills_from_orderfilled(
    table: pa.Table,
    *,
    block_lo: int,
    block_hi: int,
    short_tokens: pa.Array | None,
    timestamps: np.ndarray | None = None,
) -> tuple[pa.Table, dict[str, int]]:
    """Map one HF ``orderfilled`` slice to the R2 fill schema. ``timestamps`` overrides HF's column."""
    blocks = table.column("block_number").to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
    keep = (blocks >= block_lo) & (blocks < block_hi)
    stats = {"rows_in": int(len(table)), "rows_out_of_window": int((~keep).sum())}
    table = table.filter(pa.array(keep))
    blocks = blocks[keep]
    if len(table) == 0:
        stats.update(rows_no_usdc=0, rows_zero_shares=0, rows_short_crypto=0, rows_kept=0)
        return pa.Table.from_arrays([pa.array([], type=f.type) for f in FILL_SCHEMA], schema=FILL_SCHEMA), stats

    maker_asset = table.column("maker_asset_id").to_pylist()
    taker_asset = table.column("taker_asset_id").to_pylist()
    maker_amt = u256_to_float(table.column("maker_amount_filled"))
    taker_amt = u256_to_float(table.column("taker_amount_filled"))
    fee = (
        u256_to_float(table.column("maker_fee"))
        + u256_to_float(table.column("taker_fee"))
        + u256_to_float(table.column("protocol_fee"))
    )
    maker_is_token = np.array([str(a) != "0" for a in maker_asset])
    taker_is_token = np.array([str(a) != "0" for a in taker_asset])
    no_usdc = ~(maker_is_token ^ taker_is_token)
    asset = [str(m if str(m) != "0" else t) for m, t in zip(maker_asset, taker_asset)]
    usdc = np.where(maker_is_token, taker_amt, maker_amt)
    shares = np.where(maker_is_token, maker_amt, taker_amt)
    zero_shares = shares <= 0
    side = np.where(maker_is_token, "SELL", "BUY")

    takers = [str(t or "").lower() for t in table.column("taker").to_pylist()]
    order_type = np.where([t in EXCHANGE_ADDRESSES for t in takers], "taker", "maker")
    address = [EXCHANGE_ADDRESS.get(str(c), str(c or "").lower()) for c in table.column("contract").to_pylist()]
    makers = [str(m or "").lower() for m in table.column("maker").to_pylist()]
    txs = []
    for tx in table.column("transaction_hash").to_pylist():
        h = str(tx or "").lower()
        txs.append(h if h.startswith("0x") else "0x" + h)
    log_index = table.column("log_index").to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
    ids = [f"log_{tx}_{int(i)}" for tx, i in zip(txs, log_index)]
    if timestamps is None:
        ts = table.column("timestamp").to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
    else:
        ts = timestamps[keep] if len(timestamps) == stats["rows_in"] else timestamps
        if len(ts) != len(table):
            raise RuntimeError(f"timestamp override has {len(ts)} rows, table has {len(table)}")

    short = np.zeros(len(table), dtype=bool)
    if short_tokens is not None and len(short_tokens):
        short = np.asarray(pc.is_in(pa.array(asset), value_set=short_tokens).fill_null(False))
    drop = no_usdc | zero_shares | short
    stats["rows_no_usdc"] = int(no_usdc.sum())
    stats["rows_zero_shares"] = int((~no_usdc & zero_shares).sum())
    stats["rows_short_crypto"] = int((~(no_usdc | zero_shares) & short).sum())
    keep_i = np.flatnonzero(~drop)
    stats["rows_kept"] = int(len(keep_i))
    if len(keep_i) == 0:
        return pa.Table.from_arrays([pa.array([], type=f.type) for f in FILL_SCHEMA], schema=FILL_SCHEMA), stats

    def take_list(values: list[Any]) -> list[Any]:
        return [values[i] for i in keep_i]

    usdc_k = usdc[keep_i]
    shares_k = shares[keep_i]
    ts_k = ts[keep_i].astype(np.int64, copy=False)
    price = usdc_k / shares_k
    n = len(keep_i)
    days = (ts_k // 86400).astype(np.int32)
    out = pa.table(
        {
            "id": take_list(ids),
            "block_number": blocks[keep_i],
            "block_timestamp": ts_k,
            "transaction_hash": take_list(txs),
            "address": take_list(address),
            "user_id": take_list(makers),
            "asset": take_list(asset),
            "amount_usdc": usdc_k,
            "amount_shares": shares_k,
            "price": price,
            "tx_type": _const_string("TRADE", n),
            "side": side[keep_i].tolist(),
            "order_hash": pa.nulls(n, type=pa.string()),
            "counterparty_id": take_list(takers),
            "order_type": order_type[keep_i].tolist(),
            "fee": fee[keep_i],
            "builder": pa.nulls(n, type=pa.string()),
            "dt": pa.array(days, type=pa.date32()),
            "_gs_op": _const_string("hf", n),
        },
        schema=FILL_SCHEMA,
    )
    return out, stats


def _open_hf():
    import fsspec

    fh = fsspec.open(HF_ORDERFILLED, block_size=16 << 20).open()
    return fh, pq.ParquetFile(fh)


def _block_stats(pf: pq.ParquetFile) -> list[tuple[int, int, int]]:
    names = pf.schema_arrow.names
    col = names.index("block_number")
    out = []
    for i in range(pf.metadata.num_row_groups):
        st = pf.metadata.row_group(i).column(col).statistics
        if st is None or not st.has_min_max:
            out.append((i, 0, 2**62))
        else:
            out.append((i, int(st.min), int(st.max)))
    return out


# Backfill walks backward until this instant. Stage 1 already covers from here.
STOP_TS = 1727740800  # 2024-10-01 00:00:00 UTC


def next_ts_window(cursor_ts: int, days: int, stop_ts: int = STOP_TS) -> tuple[int, int] | None:
    """Half-open ``[start, cursor_ts)`` of at most ``days``, clipped to ``stop_ts``. None if finished."""
    if cursor_ts <= stop_ts:
        return None
    return max(stop_ts, cursor_ts - days * 86400), cursor_ts


def fill_object_name(block_lo: int, row_group: int) -> str:
    """Per-week name so a row group that spans a week boundary does not replace the other week."""
    return f"hf_w{block_lo}_rg{row_group:05d}.parquet"


def _existing_start(con) -> tuple[int, int]:
    """Oldest fill already on R2 for this period, else the local mirror of the original window."""
    remote = r2_uri("order_filled", "**", "*.parquet")
    try:
        row = con.execute(
            f"SELECT MIN(block_number), MIN(block_timestamp) FROM read_parquet('{remote}', hive_partitioning=true)"
        ).fetchone()
        if row[0] is not None:
            return int(row[0]), int(row[1])
    except Exception as exc:
        _log(f"R2 min-block lookup failed ({exc}); using the local mirror")
    mirror = "data/periods/recent_2026/r2_mirror/order_filled/**/*.parquet"
    row = con.execute(
        f"SELECT MIN(block_number), MIN(block_timestamp) FROM read_parquet('{mirror}', hive_partitioning=true)"
    ).fetchone()
    if row[0] is None:
        raise RuntimeError("No existing fills on R2 or in the local mirror")
    return int(row[0]), int(row[1])


def _short_tokens(con) -> pa.Array:
    cache = r2_uri("hf_load", "short_crypto_tokens.parquet")
    try:
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{cache}')").fetchone()[0]
        _log(f"short-crypto token cache {n:,} rows")
        toks = con.execute(f"SELECT token FROM read_parquet('{cache}')").fetchnumpy()["token"]
        return pa.array(toks)
    except Exception:
        _log("building short-crypto token list from HF markets (one scan)")
    con.execute("SET http_timeout=900")
    frame = con.execute(
        f"""
        SELECT DISTINCT token FROM (
          SELECT token1 AS token, lower(coalesce(slug, '')) AS slug FROM read_parquet('{HF_MARKETS}')
          UNION ALL
          SELECT token2, lower(coalesce(slug, '')) FROM read_parquet('{HF_MARKETS}')
        )
        WHERE slug LIKE '%updown-5m%' OR slug LIKE '%updown-15m%'
           OR slug LIKE '%up-or-down-5m%' OR slug LIKE '%up-or-down-15m%'
           OR regexp_matches(slug, '(^|-)(5m|15m)(-|$)')
        """
    ).fetchnumpy()["token"]
    table = pa.table({"token": pa.array(frame)})
    con.register("_short", table)
    con.execute(f"COPY _short TO '{cache}' (FORMAT PARQUET, COMPRESSION ZSTD, OVERWRITE_OR_IGNORE true)")
    con.unregister("_short")
    _log(f"cached {len(frame):,} short-crypto tokens at {cache}")
    return pa.array(frame)


def _timestamps_reliable(sample: list[tuple[int, int]]) -> bool:
    for block, hf_ts in sample:
        chain = block_timestamp(block)
        if abs(chain - hf_ts) > _TS_TOLERANCE_S:
            _log(f"timestamp mismatch block {block}: hf {hf_ts} chain {chain}")
            return False
    return True


def _anchor_map(block_lo: int, block_hi: int) -> tuple[np.ndarray, np.ndarray]:
    anchors = list(range(block_lo, block_hi, _ANCHOR_EVERY))
    if not anchors or anchors[-1] != block_hi - 1:
        anchors.append(block_hi - 1)
    _log(f"rebuilding timestamps from {len(anchors):,} block anchors")
    ts = np.array([block_timestamp(b) for b in anchors], dtype=np.int64)
    return np.array(anchors, dtype=np.int64), ts


def _s3():
    import pyarrow.fs as pafs

    account = os.environ["R2_ACCOUNT_ID"]
    return pafs.S3FileSystem(
        access_key=os.environ["R2_ACCESS_KEY_ID"],
        secret_key=os.environ["R2_SECRET_ACCESS_KEY"],
        endpoint_override=f"{account}.r2.cloudflarestorage.com",
        region="auto",
    )


def _delete_uri(uri: str) -> None:
    """Remove an object so a resumed row group replaces a partial upload instead of keeping it."""
    prefix = f"r2://{r2_bucket()}/"
    if not uri.startswith(prefix):
        raise RuntimeError(f"Unexpected fill URI {uri}")
    path = f"{r2_bucket()}/{uri[len(prefix):]}"
    try:
        _s3().delete_file(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        text = str(exc).lower()
        if "not found" in text or "404" in text or "nosuchkey" in text:
            return
        raise


def _write_days(con, table: pa.Table, rg: int, block_lo: int) -> int:
    """Write one file per day. Returns the number of files. A crash before the manifest save redoes this group."""
    if len(table) == 0:
        return 0
    day_ids = table.column("dt").cast(pa.int32()).to_numpy(zero_copy_only=False)
    n_files = 0
    for day in sorted(set(int(d) for d in day_ids.tolist())):
        part = table.filter(pc.equal(table.column("dt"), pa.scalar(day, type=pa.date32())))
        date = datetime.fromtimestamp(day * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
        uri = r2_uri("order_filled", f"dt={date}", fill_object_name(block_lo, rg))
        _delete_uri(uri)
        con.register("_part", part)
        try:
            con.execute(f"COPY _part TO '{uri}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        finally:
            con.unregister("_part")
        n_files += 1
    return n_files


def _sample_timestamps(pf: pq.ParquetFile, rg: int) -> list[tuple[int, int]]:
    tiny = pf.read_row_group(rg, columns=["block_number", "timestamp"])
    blocks = tiny.column("block_number").to_numpy(zero_copy_only=False)
    ts = tiny.column("timestamp").to_numpy(zero_copy_only=False)
    if len(blocks) == 0:
        return []
    picks = np.linspace(0, len(blocks) - 1, num=min(5, len(blocks)), dtype=int)
    return [(int(blocks[i]), int(ts[i])) for i in picks]


def _manifest() -> Manifest:
    return Manifest(Path("data") / "periods" / period_id() / "hf_load_manifest.json")


def _seed_cursor(manifest: Manifest, con) -> None:
    """Remember how far the backfill has reached. The first trial week lives in the legacy keys."""
    if manifest.get("cursor_block") is not None:
        return
    stats = manifest.get("stats") or {}
    if stats.get("verify_block_min") == manifest.get("block_lo"):
        done = list(manifest.get("completed_weeks") or [])
        done.append(
            {
                "block_lo": manifest.get("block_lo"),
                "block_hi": manifest.get("block_hi"),
                "rows": stats.get("verify_rows"),
                "ts_min": stats.get("verify_ts_min"),
                "ts_max": stats.get("verify_ts_max"),
                "name": "hf_rg*.parquet",
            }
        )
        manifest.data["completed_weeks"] = done
        manifest.data["cursor_block"] = manifest.get("block_lo")
        manifest.data["cursor_ts"] = stats.get("verify_ts_min")
        manifest.data["current"] = None
        manifest.save()
        _log(f"trial week already on R2; cursor at block {manifest.get('cursor_block')}")
        return
    block, ts = _existing_start(con)
    manifest.data["cursor_block"] = block
    manifest.data["cursor_ts"] = ts
    manifest.data["current"] = None
    manifest.save()


def _plan_week(manifest: Manifest, days: int) -> dict[str, Any] | None:
    """Return the in-progress week, or open the next older one. None when ``STOP_TS`` is reached."""
    current = manifest.get("current")
    if current and current.get("status") == "in_progress":
        return current
    window = next_ts_window(int(manifest.get("cursor_ts")), days)
    if window is None:
        return None
    ts_min, ts_max = window
    lo, hi = default_search_bounds(ts_min)
    block_lo = find_block_at_or_before(ts_min - 1, lo, hi) + 1
    block_hi = int(manifest.get("cursor_block"))
    if block_lo >= block_hi:
        return None
    current = {
        "status": "in_progress",
        "block_lo": block_lo,
        "block_hi": block_hi,
        "ts_min": ts_min,
        "ts_max": ts_max,
        "completed_row_groups": [],
        "timestamps_reliable": None,
        "anchors": None,
        "stats": {},
        "files_written": 0,
    }
    manifest.data["current"] = current
    manifest.save()
    return current


def load_one_week(days: int = 7) -> str:
    """Load one week ending at the backfill cursor. Returns ``week`` or ``done``.

    Progress is the local manifest: a row group counts only after its parquet files are on R2.
    Restarting repeats the unfinished group and then continues older weeks.
    """
    load_env()
    os.environ.pop("LOCAL_R2_MIRROR", None)
    os.environ["PERIOD_ID"] = "recent_2026"
    if not period_id():
        raise RuntimeError("Set PERIOD_ID so fills land under periods/<id>/ and not on the Stage 1 prefix")
    con = get_duck()
    con.execute(
        "SET enable_progress_bar=false; SET http_timeout=900; SET memory_limit='8GB'; "
        "SET max_temp_directory_size='1GB'"
    )
    manifest = _manifest()
    _seed_cursor(manifest, con)
    current = _plan_week(manifest, days)
    if current is None:
        _log(f"backfill reached {datetime.fromtimestamp(STOP_TS, timezone.utc):%Y-%m-%d}Z")
        return "done"
    block_lo, block_hi = int(current["block_lo"]), int(current["block_hi"])
    ts_min, ts_max = int(current["ts_min"]), int(current["ts_max"])
    _log(
        f"week blocks [{block_lo}, {block_hi})  "
        f"{datetime.fromtimestamp(ts_min, timezone.utc):%Y-%m-%d %H:%M}Z .. "
        f"{datetime.fromtimestamp(ts_max, timezone.utc):%Y-%m-%d %H:%M}Z"
    )

    short = _short_tokens(con)
    fh, pf = _open_hf()
    groups = row_groups_overlapping(_block_stats(pf), block_lo, block_hi)
    done = set(current.get("completed_row_groups") or [])
    _log(f"{len(groups)} overlapping row groups, {len(done)} already loaded")
    pending = [g for g in groups if g not in done]

    reliable = current.get("timestamps_reliable")
    anchors = anchor_ts = None
    if current.get("anchors"):
        pairs = current["anchors"]
        anchors = np.array([p[0] for p in pairs], dtype=np.int64)
        anchor_ts = np.array([p[1] for p in pairs], dtype=np.int64)
    elif reliable is False:
        anchors, anchor_ts = _anchor_map(block_lo, block_hi)
        current["anchors"] = [[int(a), int(t)] for a, t in zip(anchors, anchor_ts)]
        manifest.data["current"] = current
        manifest.save()
    if pending and reliable is None:
        sample = _sample_timestamps(pf, pending[0])
        # Spot-check blocks inside this week; a row group can extend outside it.
        sample = [(b, t) for b, t in sample if block_lo <= b < block_hi] or sample
        reliable = _timestamps_reliable(sample)
        current["timestamps_reliable"] = reliable
        current["timestamp_sample"] = [{"block": b, "hf_ts": t} for b, t in sample]
        if not reliable and anchors is None:
            anchors, anchor_ts = _anchor_map(block_lo, block_hi)
            current["anchors"] = [[int(a), int(t)] for a, t in zip(anchors, anchor_ts)]
        manifest.data["current"] = current
        manifest.save()
        _log(f"HF timestamps {'match' if reliable else 'do not match'} the chain")

    totals = dict(current.get("stats") or {})
    t0 = time.monotonic()
    try:
        for n, rg in enumerate(pending, 1):
            started = time.monotonic()
            for attempt in range(4):
                try:
                    raw = pf.read_row_group(rg, columns=READ_COLUMNS)
                    break
                except Exception as exc:
                    _log(f"row group {rg} read failed ({exc}); reopening ({attempt + 1})")
                    time.sleep(min(60, 2 * (attempt + 1)))
                    try:
                        fh.close()
                    except Exception:
                        pass
                    fh, pf = _open_hf()
            else:
                raise RuntimeError(f"giving up on row group {rg}")
            ts_override = None
            if not reliable:
                b = raw.column("block_number").to_numpy(zero_copy_only=False).astype(np.float64)
                ts_override = interpolate_timestamps(b, anchors, anchor_ts)
            part, stats = fills_from_orderfilled(
                raw, block_lo=block_lo, block_hi=block_hi, short_tokens=short, timestamps=ts_override
            )
            n_files = _write_days(con, part, rg, block_lo)
            for key, val in stats.items():
                totals[key] = int(totals.get(key, 0)) + int(val)
            totals["files_written"] = int(totals.get("files_written", 0)) + n_files
            done.add(int(rg))
            current["completed_row_groups"] = sorted(done)
            current["stats"] = totals
            manifest.data["current"] = current
            manifest.save()
            _log(
                f"group {rg} ({n}/{len(pending)}) kept {stats['rows_kept']:,} "
                f"dropped short {stats['rows_short_crypto']:,} in {time.monotonic() - started:.0f}s"
            )
    finally:
        try:
            fh.close()
        except Exception:
            pass

    totals["elapsed_s"] = round(float(totals.get("elapsed_s", 0)) + time.monotonic() - t0, 1)
    kept = int(totals.get("rows_kept", 0))
    if kept == 0:
        check = (0, 0, block_lo, block_hi - 1, ts_min, ts_max - 1)
    else:
        src = r2_uri("order_filled", "**", fill_object_name(block_lo, 0).replace("rg00000", "rg*"))
        check = con.execute(
            f"""
            SELECT COUNT(*) AS n, COUNT(DISTINCT id) AS ids,
                   MIN(block_number) AS bmin, MAX(block_number) AS bmax,
                   MIN(block_timestamp) AS tmin, MAX(block_timestamp) AS tmax
            FROM read_parquet('{src}', hive_partitioning=true)
            """
        ).fetchone()
    totals["verify_rows"] = int(check[0] or 0)
    totals["verify_ids"] = int(check[1] or 0)
    if totals["verify_rows"] != totals["verify_ids"]:
        raise RuntimeError(f"duplicate ids in week {block_lo}: {totals['verify_rows'] - totals['verify_ids']}")
    if totals["verify_rows"] != kept:
        raise RuntimeError(f"R2 has {totals['verify_rows']} rows, manifest kept {kept}")
    if kept and (int(check[2]) < block_lo or int(check[3]) >= block_hi):
        raise RuntimeError(f"written blocks [{check[2]}, {check[3]}] outside [{block_lo}, {block_hi})")
    weeks = list(manifest.get("completed_weeks") or [])
    weeks.append(
        {
            "block_lo": block_lo,
            "block_hi": block_hi,
            "ts_min": ts_min,
            "ts_max": ts_max,
            "rows": kept,
            "verify_ts_min": int(check[4] or 0),
            "verify_ts_max": int(check[5] or 0),
        }
    )
    current["status"] = "complete"
    current["stats"] = totals
    manifest.data["completed_weeks"] = weeks
    manifest.data["cursor_block"] = block_lo
    manifest.data["cursor_ts"] = ts_min
    manifest.data["current"] = None
    manifest.save()
    _log(f"week saved {totals}")
    return "week"


def main() -> None:
    """Load one week and exit 2 so a supervisor can start the next. Exit 0 when the backfill is finished."""
    days = int(os.environ.get("HF_LOAD_DAYS", "7"))
    sys.exit(0 if load_one_week(days) == "done" else 2)


if __name__ == "__main__":
    main()

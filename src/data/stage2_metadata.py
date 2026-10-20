"""Stage 2 helpers for already-ingested fills: Gamma metadata, R2 publishing, RPC spot checks.

Fill ingest itself is not done here. Fills come from existing R2 slices or the HuggingFace
Polymarket_data release (see ``scripts/validate_hf_sample.py``).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.data.load_markets import build_tables_from_markets, fetch_keyset_pages, gamma_client
from src.lib.checkpoint import Manifest, PageStore, stable_key
from src.lib.duck import r2_uri
from src.lib.http import EdgeBlockedError
from src.lib.period import local_dir, period_key
from src.lib.polygon import default_search_bounds, find_block_at_or_before, polygon_rpc

V2_EXCHANGES = {
    "0xe111180000d2663c0091e4f400237545b87b996b": "ctf_exchange_v2",
    "0xe2222d279d744050d28e00520010520000310f59": "neg_risk_ctf_exchange_v2",
    "0xe3333700ca9d93003f00f0f71f8515005f6c00aa": "exchange_v3_combos",
}
COMBO_EXCHANGE = "0xe3333700ca9d93003f00f0f71f8515005f6c00aa"
ORDER_FILLED_V2_TOPIC0 = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)).strip() or default)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _progress(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}Z] {msg}", file=sys.stderr, flush=True)


def first_block_at_or_after(ts: int) -> int:
    lo, hi = default_search_bounds(ts)
    return find_block_at_or_before(ts - 1, lo, hi) + 1


def decode_order_filled_token_id(log: dict[str, Any]) -> str:
    """V2 OrderFilled data words: side, tokenId, makerAmt, takerAmt, fee, builder, metadata."""
    data = log["data"][2:]
    return str(int(data[64:128], 16))


def fetch_events_windowed(
    client, params: dict[str, Any], store: PageStore, lo_ts: int, hi_ts: int, *, min_span_s: int = 900
) -> tuple[list[dict], dict[str, int]]:
    """Keyset events with end dates in [lo_ts, hi_ts); halves the window on persistent edge blocks.

    Some keyset cursors deterministically trip Gamma's CDN WAF; narrower windows yield new cursors.
    """
    q = {**params, "end_date_min": _iso(lo_ts), "end_date_max": _iso(hi_ts - 1)}
    try:
        return fetch_keyset_pages(client, "events/keyset", q, store, items_key="events")
    except EdgeBlockedError:
        if hi_ts - lo_ts <= min_span_s:
            raise
        _progress(f"edge block on {params.get('tag_slug')} {_iso(lo_ts)}..{_iso(hi_ts)}; splitting window")
        mid = lo_ts + (hi_ts - lo_ts) // 2
        out: list[dict] = []
        stats = {"pages_fetched": 0, "pages_reused": 0, "rows": 0, "splits": 1}
        for a, b in ((lo_ts, mid), (mid, hi_ts)):
            rows, st = fetch_events_windowed(client, params, store, a, b, min_span_s=min_span_s)
            out.extend(rows)
            for k in stats:
                stats[k] += st.get(k, 0)
        return out, stats


def fetch_short_crypto_tokens(
    start_ts: int, end_ts: int, recurrences: list[str], lookahead_h: int
) -> tuple[set[str], dict[str, Any]]:
    base = os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    store = PageStore(local_dir("gamma", "short_crypto_events"))
    tokens: set[str] = set()
    per_series: dict[str, int] = {}
    stats: dict[str, Any] = {}
    with gamma_client(base) as client:
        for rec in recurrences:
            for closed in ("true", "false"):
                params = {"tag_slug": rec.upper(), "closed": closed, "limit": 200}
                events, st = fetch_events_windowed(
                    client, params, store, start_ts - 3600, end_ts + lookahead_h * 3600 + 1
                )
                stats[f"{rec}_{closed}"] = st
                for ev in events:
                    ev_rec = {str(s.get("recurrence")) for s in ev.get("series") or []}
                    if ev_rec and rec not in ev_rec:
                        stats["skipped_other_recurrence"] = stats.get("skipped_other_recurrence", 0) + 1
                        continue
                    series = (ev.get("seriesSlug") or str(ev.get("slug", "")).rsplit("-", 1)[0])
                    per_series[series] = per_series.get(series, 0) + 1
                    for m in ev.get("markets") or []:
                        raw = m.get("clobTokenIds")
                        ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
                        tokens.update(str(t) for t in ids)
        stats["http"] = client.stats.as_dict()
    stats["events_per_series"] = per_series
    return tokens, stats


def _token_ids(market: dict[str, Any]) -> list[str]:
    raw = market.get("clobTokenIds")
    return [str(t) for t in (json.loads(raw) if isinstance(raw, str) else (raw or []))]


def fetch_tag_window_tokens(
    start_ts: int, end_ts: int, tags: list[str], lookahead_days: int, min_volume: float
) -> tuple[dict[str, float], dict[str, Any]]:
    """token -> market volume for markets (volume >= ``min_volume``) in ``tags`` events ending in [start-1h, end+lookahead].

    Pages are keyed by (tag, closed, UTC end-date day) so neighbouring slices reuse them.
    Gamma end dates trail the real game (tennis: ~7 days), hence the multi-day lookahead.
    """
    base = os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    store = PageStore(local_dir("gamma", "tag_events_by_day"))
    day0 = (start_ts - 3600) // 86400
    day1 = (end_ts + lookahead_days * 86400) // 86400
    volume: dict[str, float] = {}
    stats: dict[str, Any] = {"pages_fetched": 0, "pages_reused": 0, "events": 0, "markets_below_min_volume": 0}
    with gamma_client(base) as client:
        for tag in tags:
            for day in range(day0, day1 + 1):
                for closed in ("true", "false"):
                    params = {"tag_slug": tag, "closed": closed, "limit": 100}
                    events, st = fetch_events_windowed(client, params, store, day * 86400, (day + 1) * 86400)
                    stats["pages_fetched"] += st["pages_fetched"]
                    stats["pages_reused"] += st["pages_reused"]
                    stats["events"] += len(events)
                    for ev in events:
                        for m in ev.get("markets") or []:
                            if float(m.get("volumeNum") or 0) < max(min_volume, 1e-9):
                                stats["markets_below_min_volume"] += 1
                                continue
                            vol = float(m.get("volumeNum") or 0)
                            for t in _token_ids(m):
                                volume[t] = max(vol, volume.get(t, 0.0))
        stats["http"] = client.stats.as_dict()
    stats["tokens"] = len(volume)
    return volume, stats


def token_tag_index() -> dict[str, set[str]]:
    """token_id -> market tag slugs, from the cached Gamma market pages."""
    store = PageStore(local_dir("gamma", "markets_by_token"))
    out: dict[str, set[str]] = {}
    for key in store.keys():
        for m in store.load(key).get("markets") or []:
            slugs = {str(t.get("slug")) for t in (m.get("tags") or []) if isinstance(t, dict)}
            for t in _token_ids(m):
                out[t] = slugs
    return out


def slice_assets_path(bs: int, be: int) -> Path:
    return local_dir("slices") / f"assets_{bs}_{be}.json"




def load_slice_fills(con, prefix: str) -> None:
    """Materialize one slice from R2 into the in-memory ``slice_fills`` table (one read)."""
    glob = r2_uri(prefix, "**", "*.parquet")
    for kind in ("VIEW", "TABLE"):
        try:
            con.execute(f"DROP {kind} IF EXISTS slice_fills")
        except duckdb.CatalogException:
            pass
    con.execute(f"CREATE TABLE slice_fills AS SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")


def verify_slice(con, prefix: str, bs: int, be: int, excluded: set[str], spot_blocks: int) -> dict[str, Any]:
    load_slice_fills(con, prefix)
    q = con.execute(
        """
        SELECT COUNT(*) n, COUNT(DISTINCT id) n_ids, MIN(block_number) bmin, MAX(block_number) bmax,
          COUNT(DISTINCT block_number) n_blocks, MIN(block_timestamp) tmin, MAX(block_timestamp) tmax,
          SUM(CASE WHEN user_id IS NULL OR asset IS NULL OR price IS NULL THEN 1 ELSE 0 END) n_null,
          SUM(CASE WHEN price < 0 OR price > 1 THEN 1 ELSE 0 END) n_bad_price,
          SUM(CASE WHEN lower(order_type)='taker' THEN 1 ELSE 0 END) n_taker,
          SUM(CASE WHEN lower(order_type)='maker' THEN 1 ELSE 0 END) n_maker,
          SUM(amount_usdc) usdc, SUM(COALESCE(fee,0)) fees, COUNT(DISTINCT asset) n_assets,
          COUNT(DISTINCT user_id) n_users
        FROM slice_fills
        """
    ).fetchone()
    cols = ["rows", "distinct_ids", "block_min", "block_max", "distinct_blocks", "ts_min", "ts_max",
            "null_critical", "bad_price", "taker_rows", "maker_rows", "usdc", "fees", "distinct_assets",
            "distinct_users"]
    out: dict[str, Any] = {c: (float(v) if isinstance(v, float) else (int(v) if v is not None else None))
                           for c, v in zip(cols, q)}
    by_addr = con.execute("SELECT lower(address) a, COUNT(*) n FROM slice_fills GROUP BY 1").df()
    out["rows_by_contract"] = {V2_EXCHANGES.get(a, a): int(n) for a, n in zip(by_addr["a"], by_addr["n"])}
    con.register("_excl", pd.DataFrame({"asset": sorted(excluded)}))
    out["excluded_token_rows"] = int(
        con.execute("SELECT COUNT(*) FROM slice_fills f JOIN _excl e ON e.asset = CAST(f.asset AS VARCHAR)").fetchone()[0]
    )
    con.unregister("_excl")

    rpc = polygon_rpc()
    span = max(1, be - bs)
    picks = sorted({bs + (span * (k + 1)) // (spot_blocks + 1) for k in range(spot_blocks)})
    picks = [bs, *picks, be]
    r2_counts = con.execute(
        f"SELECT block_number, COUNT(*) n FROM slice_fills WHERE block_number IN ({','.join(map(str, picks))}) GROUP BY 1"
    ).df()
    r2_map = dict(zip(r2_counts["block_number"].astype(int), r2_counts["n"].astype(int)))
    addresses = sorted(set(V2_EXCHANGES) | {str(a) for a in by_addr["a"]})
    out["spot_check_addresses"] = addresses
    spot = []
    for b in picks:
        logs = rpc.call(
            "eth_getLogs",
            [{"fromBlock": hex(b), "toBlock": hex(b), "address": addresses, "topics": [ORDER_FILLED_V2_TOPIC0]}],
        )
        kept = sum(1 for lg in logs if decode_order_filled_token_id(lg) not in excluded)
        spot.append({"block": b, "rpc_logs": len(logs), "rpc_kept": kept, "r2_rows": r2_map.get(b, 0)})
    out["spot_checks"] = spot
    out["spot_mismatches"] = sum(1 for s in spot if s["rpc_kept"] != s["r2_rows"])
    out["rpc_stats"] = rpc.stats()
    return out


def fetch_metadata_for_tokens(tokens: list[str], batch: int, *, force: bool = False) -> dict[str, Any]:
    """Look up Gamma markets for unseen tokens (open pass, then closed pass); cached by token."""
    base = os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    store = PageStore(local_dir("gamma", "markets_by_token"))
    index_path = local_dir("gamma") / "token_index.json"
    index: dict[str, str] = json.loads(index_path.read_text()) if index_path.exists() else {}
    todo = sorted(set(tokens)) if force else sorted(t for t in tokens if index.get(t) != "found")
    stats: dict[str, Any] = {"tokens_requested": len(tokens), "tokens_unseen": len(todo)}
    with gamma_client(base) as client:
        # A forced refresh asks only for closed markets, so a market that has since
        # resolved replaces the open page cached during ingest.
        for closed in (("true",) if force else (None, "true")):
            pending = list(todo) if force else [t for t in todo if index.get(t) != "found"]
            def lookup(chunk: list[str]) -> None:
                params = [("clob_token_ids", t) for t in chunk] + [("limit", 100), ("include_tag", "true")]
                if closed:
                    params.append(("closed", closed))
                key = stable_key("markets/keyset", params)
                if store.has(key) and not force:
                    page = store.load(key)
                else:
                    try:
                        page = client.get_json("markets/keyset", params=params)
                    except RuntimeError:
                        if len(chunk) == 1:
                            index[chunk[0]] = "error"
                            stats["tokens_error"] = stats.get("tokens_error", 0) + 1
                            return
                        _progress(f"markets/keyset failing for {len(chunk)} tokens; bisecting")
                        lookup(chunk[: len(chunk) // 2])
                        lookup(chunk[len(chunk) // 2 :])
                        return
                    store.save(key, page)
                for m in page.get("markets") or []:
                    for t in _token_ids(m):
                        index[t] = "found"
                for t in chunk:
                    index.setdefault(t, "missing")

            for i in range(0, len(pending), batch):
                lookup(pending[i : i + batch])
                index_path.write_text(json.dumps(index))
        stats["http"] = client.stats.as_dict()
    stats["tokens_missing"] = sum(1 for t in tokens if index.get(t) != "found")
    return stats


def publish_metadata(con) -> dict[str, int]:
    store = PageStore(local_dir("gamma", "markets_by_token"))
    markets: dict[str, dict] = {}
    for key in store.keys():
        for m in store.load(key).get("markets") or []:
            mid = str(m.get("id"))
            prev = markets.get(mid)
            if prev is None or (m.get("closed") and not prev.get("closed")):
                markets[mid] = m
    events_df, markets_df, tokens_df = build_tables_from_markets(list(markets.values()))
    for name, df in (("events", events_df), ("markets", markets_df), ("outcome_tokens", tokens_df)):
        con.register("_meta", df)
        con.execute(f"COPY _meta TO '{r2_uri(period_key('metadata', name + '.parquet'))}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
        con.unregister("_meta")
    return {"events": len(events_df), "markets": len(markets_df), "outcome_tokens": len(tokens_df)}


def mirror_manifest(con, manifest: Manifest) -> str:
    uri = r2_uri(period_key("manifests", "ingest_manifest.parquet"))
    con.register("_mf", pd.DataFrame({"manifest_json": [json.dumps(manifest.data, sort_keys=True)]}))
    con.execute(f"COPY _mf TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
    con.unregister("_mf")
    return uri


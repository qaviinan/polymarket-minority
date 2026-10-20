"""Fetch Polymarket Gamma /events and write metadata Parquet to R2."""

from __future__ import annotations

import json
import os
import random
from datetime import date, datetime
from typing import Any

import pandas as pd

from src.lib.checkpoint import PageStore, stable_key
from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri
from src.lib.http import PoliteClient
from src.lib.period import local_dir, period_key


def _parse_json_field(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _winner_index(outcomes: list[Any], outcome_prices: list[Any]) -> int | None:
    if not outcomes or not outcome_prices:
        return None
    try:
        prices = [float(str(p)) for p in outcome_prices]
    except (TypeError, ValueError):
        return None
    if len(prices) != len(outcomes):
        return None
    # Winner is typically price ~1 after resolution
    mx = max(prices)
    if mx < 0.5:  # not resolved / ambiguous
        return None
    return int(prices.index(mx))


def _extract_tokens_and_labels(market: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return parallel lists (token_ids, outcome_labels)."""
    raw_ids = market.get("clobTokenIds") or market.get("clob_token_ids")
    raw_ids = _parse_json_field(raw_ids)
    if isinstance(raw_ids, str):
        raw_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
    if not isinstance(raw_ids, list):
        raw_ids = []

    outcomes = _parse_json_field(market.get("outcomes"))
    if isinstance(outcomes, str):
        outcomes = [outcomes]
    if not isinstance(outcomes, list):
        outcomes = []

    # Normalize token ids to string (match on-chain string form)
    token_ids = [str(t).strip() for t in raw_ids if str(t).strip()]
    labels = [str(o) for o in outcomes]
    return token_ids, labels


def _market_in_window(m: dict[str, Any], d0: date, d1: date) -> bool:
    if not m.get("closed"):
        return False
    ed = m.get("endDate") or m.get("end_date")
    if not ed:
        return False
    try:
        if isinstance(ed, str):
            dt = datetime.fromisoformat(ed.replace("Z", "+00:00")).date()
        else:
            return False
    except ValueError:
        return False
    return d0 <= dt <= d1


def gamma_client(base_url: str) -> PoliteClient:
    interval = float(os.environ.get("GAMMA_MIN_INTERVAL_S", "0.5"))
    return PoliteClient(base_url, min_interval_s=interval, max_retries=6, timeout_s=120.0)


def fetch_keyset_pages(
    client: PoliteClient,
    endpoint: str,
    params: list[tuple[str, Any]] | dict[str, Any],
    store: PageStore,
    *,
    items_key: str,
    max_pages: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Follow Gamma keyset pages (``after_cursor``); each page is checkpointed and reused.

    The cursor chain is resumable: page N is stored under a key derived from the query and
    the cursor that produced it, so a crashed run re-reads saved pages without new requests.
    """
    base_params = list(params.items()) if isinstance(params, dict) else list(params)
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    fetched = reused = 0
    pages = 0
    while True:
        key = stable_key(endpoint, base_params, cursor)
        if store.has(key):
            page = store.load(key)
            reused += 1
        else:
            q = base_params + ([("after_cursor", cursor)] if cursor else [])
            page = client.get_json(endpoint, params=q)
            store.save(key, page)
            fetched += 1
        rows.extend(page.get(items_key) or [])
        pages += 1
        cursor = page.get("next_cursor")
        if not cursor or not page.get(items_key) or (max_pages and pages >= max_pages):
            break
    return rows, {"pages_fetched": fetched, "pages_reused": reused, "rows": len(rows)}


def fetch_events_gamma(
    base_url: str,
    d0: date,
    d1: date,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Closed events with end_date in [d0, d1] via keyset pages (offset paging is capped)."""
    store = PageStore(local_dir("gamma", "events_closed_window"))
    params = {
        "closed": "true",
        "end_date_min": d0.isoformat(),
        "end_date_max": d1.isoformat(),
        "limit": limit,
    }
    with gamma_client(base_url) as client:
        rows, _ = fetch_keyset_pages(client, "events/keyset", params, store, items_key="events")
    return rows


def build_tables(
    raw_events: list[dict[str, Any]],
    d0: date,
    d1: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events_rows: list[dict[str, Any]] = []
    markets_rows: list[dict[str, Any]] = []
    tokens_rows: list[dict[str, Any]] = []

    for ev in raw_events:
        eid = ev.get("id")
        if eid is None:
            continue
        markets = ev.get("markets") or []
        kept_markets: list[dict[str, Any]] = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            if not _market_in_window(m, d0, d1):
                continue
            kept_markets.append(m)
        if not kept_markets:
            continue

        tags = ev.get("tags") or []
        cat = "Other"
        if isinstance(tags, list) and tags:
            t0 = tags[0]
            if isinstance(t0, dict):
                cat = str(t0.get("slug") or t0.get("label") or "Other")
            else:
                cat = str(t0)

        events_rows.append(
            {
                "event_id": str(eid),
                "slug": ev.get("slug"),
                "title": ev.get("title"),
                "end_date": ev.get("endDate") or ev.get("end_date"),
                "category": cat,
            }
        )

        for m in kept_markets:
            mid = m.get("id") or m.get("conditionId")
            if mid is None:
                continue
            mid = str(mid)
            token_ids, labels = _extract_tokens_and_labels(m)
            prices_raw = _parse_json_field(m.get("outcomePrices") or m.get("outcome_prices"))
            if isinstance(prices_raw, list):
                prices = prices_raw
            else:
                prices = []

            win_idx = _winner_index(labels if labels else list(range(len(prices))), prices)
            if win_idx is None and len(token_ids) == 2 and len(prices) == 2:
                win_idx = _winner_index(["a", "b"], prices)

            # Skip markets we cannot resolve
            if win_idx is None or len(token_ids) < 2:
                continue
            if len(token_ids) != len(labels) and len(labels) >= 2:
                # Align lengths: sometimes more labels than tokens
                token_ids = token_ids[: len(labels)]
            if len(token_ids) < 2:
                continue

            yes_token_id = str(token_ids[0])  # Polymarket convention: index 0 = Yes for binary
            if len(labels) >= 2 and str(labels[0]).lower() != "yes":
                # try find Yes index
                for i, lab in enumerate(labels):
                    if str(lab).lower() == "yes":
                        yes_token_id = str(token_ids[i])
                        break

            markets_rows.append(
                {
                    "market_id": mid,
                    "event_id": str(eid),
                    "slug": m.get("slug"),
                    "question": m.get("question"),
                    "end_date": m.get("endDate") or m.get("end_date"),
                    "resolved_outcome_index": win_idx,
                    "outcome_token_ids": json.dumps(token_ids),
                    "volume": float(m.get("volume") or 0),
                    "yes_token_id": yes_token_id,
                }
            )

            for i, tid in enumerate(token_ids):
                if i >= len(labels):
                    lab = f"outcome_{i}"
                else:
                    lab = str(labels[i])
                payout = 1.0 if i == win_idx else 0.0
                tokens_rows.append(
                    {
                        "token_id": str(tid),
                        "market_id": mid,
                        "outcome_label": lab,
                        "resolved_payout": payout,
                    }
                )

    events_df = pd.DataFrame(events_rows).drop_duplicates(subset=["event_id"])
    markets_df = pd.DataFrame(markets_rows).drop_duplicates(subset=["market_id"])
    tokens_df = pd.DataFrame(tokens_rows).drop_duplicates(subset=["token_id"])
    return events_df, markets_df, tokens_df


def _category_from_tags(tags: Any) -> str:
    generic = {"all", "hide-from-new", "recurring", "featured", "trending"}
    if isinstance(tags, list):
        for t in tags:
            slug = str(t.get("slug") or t.get("label") or "") if isinstance(t, dict) else str(t)
            if slug and slug.lower() not in generic:
                return slug
    return "Other"


def build_tables_from_markets(
    raw_markets: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Stage 2 tables from Gamma market objects (open markets kept with NULL payouts).

    A market counts as resolved only when it is closed and one outcome price is ~1;
    open-market prices are beliefs, not payouts, so they are never mapped to a winner.
    """
    events: dict[str, dict[str, Any]] = {}
    markets_rows: list[dict[str, Any]] = []
    tokens_rows: list[dict[str, Any]] = []
    for m in raw_markets:
        mid = m.get("id") or m.get("conditionId")
        if mid is None:
            continue
        ev = (m.get("events") or [{}])[0] or {}
        eid = str(ev.get("id")) if ev.get("id") is not None else f"market_{mid}"
        events.setdefault(
            eid,
            {
                "event_id": eid,
                "slug": ev.get("slug"),
                "title": ev.get("title"),
                "end_date": ev.get("endDate"),
                "category": _category_from_tags(m.get("tags") or ev.get("tags")),
                "series_slug": ev.get("seriesSlug"),
            },
        )
        token_ids, labels = _extract_tokens_and_labels(m)
        if len(token_ids) < 2:
            continue
        prices_raw = _parse_json_field(m.get("outcomePrices"))
        prices: list[float] = []
        if isinstance(prices_raw, list):
            try:
                prices = [float(str(p)) for p in prices_raw]
            except ValueError:
                prices = []
        closed = bool(m.get("closed"))
        win_idx = None
        fractional: list[float] | None = None
        if closed and len(prices) == len(token_ids) and prices and min(prices) >= 0:
            if max(prices) >= 0.99:
                win_idx = int(prices.index(max(prices)))
            elif abs(sum(prices) - 1.0) <= 0.02:
                # Closed 50/50 (or other split) resolutions. Open markets also sum to ~1,
                # so this branch stays behind the closed check.
                fractional = [p / sum(prices) for p in prices]
        yes_token_id = str(token_ids[0])
        for i, lab in enumerate(labels):
            if str(lab).lower() == "yes" and i < len(token_ids):
                yes_token_id = str(token_ids[i])
                break
        markets_rows.append(
            {
                "market_id": str(mid),
                "event_id": eid,
                "condition_id": m.get("conditionId"),
                "slug": m.get("slug"),
                "question": m.get("question"),
                "end_date": m.get("endDate"),
                "closed": closed,
                "uma_resolution_status": m.get("umaResolutionStatus"),
                "resolved_outcome_index": win_idx,
                "outcome_token_ids": json.dumps(token_ids),
                "volume": float(m.get("volumeNum") or m.get("volume") or 0),
                "yes_token_id": yes_token_id,
                "neg_risk": bool(m.get("negRisk")),
                "fees_enabled": bool(m.get("feesEnabled")),
                "fee_type": m.get("feeType"),
            }
        )
        for i, tid in enumerate(token_ids):
            tokens_rows.append(
                {
                    "token_id": str(tid),
                    "market_id": str(mid),
                    "outcome_label": str(labels[i]) if i < len(labels) else f"outcome_{i}",
                    "resolved_payout": (
                        fractional[i] if fractional is not None
                        else (1.0 if i == win_idx else 0.0) if win_idx is not None
                        else None
                    ),
                }
            )
    events_df = pd.DataFrame(list(events.values()))
    markets_df = pd.DataFrame(markets_rows).drop_duplicates(subset=["market_id"])
    tokens_df = pd.DataFrame(tokens_rows).drop_duplicates(subset=["token_id"])
    if not tokens_df.empty:
        tokens_df["resolved_payout"] = tokens_df["resolved_payout"].astype("float64")
    return events_df, markets_df, tokens_df


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    d0 = date.fromisoformat(os.environ.get("GAMMA_END_DATE_MIN", "2024-10-01"))
    d1 = date.fromisoformat(os.environ.get("GAMMA_END_DATE_MAX", "2024-12-31"))
    base = os.environ.get("POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com")

    with debug_step("load_markets") as dbg:
        dbg.log("meta", "gamma_base", base)
        dbg.log("meta", "window", f"{d0}..{d1}")
        raw = fetch_events_gamma(base, d0, d1)
        dbg.log("ingest", "raw_event_pages", len(raw))

        events_df, markets_df, tokens_df = build_tables(raw, d0, d1)
        dbg.log("ingest", "n_events", len(events_df))
        dbg.log("ingest", "n_markets", len(markets_df))
        dbg.log("ingest", "n_outcome_tokens", len(tokens_df))

        if markets_df.empty:
            dbg.assertion("empty_universe", "red", "No markets after filter — check Gamma API params")
            raise SystemExit("No markets to export")

        # Category histogram
        cat_hist = events_df["category"].value_counts().head(20)
        ch = cat_hist.reset_index()
        ch.columns = ["category", "count"]
        dbg.save_diagnostic_df("category_histogram", ch)

        # Markets per event
        mpe = markets_df.groupby("event_id").size().reset_index(name="n_markets")
        dbg.save_diagnostic_df("markets_per_event", mpe)
        med = float(mpe["n_markets"].median())
        dbg.log("quality", "median_markets_per_event", med)
        if med <= 1.0:
            dbg.assertion("cluster_size", "yellow", "Median markets per event <= 1 — event-level randomization weakly identified")
        else:
            dbg.assertion("cluster_size", "green", f"Median markets per event = {med}")

        # Payout sums per market
        ps = tokens_df.groupby("market_id")["resolved_payout"].sum().reset_index()
        bad = ps[(ps["resolved_payout"] < 0.99) | (ps["resolved_payout"] > 1.01)]
        dbg.log("quality", "markets_payout_sum_not_1", len(bad))
        if len(bad) > 0:
            dbg.save_diagnostic_df("payout_sum_anomalies", bad)
            dbg.assertion("payout_sums", "yellow", f"{len(bad)} markets with payout sum != 1")
        else:
            dbg.assertion("payout_sums", "green", "All market payout sums ~= 1")

        dup_tok = tokens_df["token_id"].duplicated().sum()
        dbg.log("quality", "duplicate_token_ids", int(dup_tok))
        if dup_tok:
            dbg.assertion("unique_tokens", "red", "Duplicate token_id rows")
        else:
            dbg.assertion("unique_tokens", "green", "No duplicate token_ids")

        # Top events by volume
        vol = markets_df.groupby("event_id")["volume"].sum().sort_values(ascending=False).head(20)
        top_ev = vol.reset_index().merge(events_df, on="event_id", how="left")
        dbg.save_diagnostic_df("top_20_events_by_volume", top_ev)

        # Sample events JSON
        sample = random.sample(list(events_df.to_dict("records")), min(5, len(events_df)))
        dbg.save_diagnostic_json("sample_events", sample)

        con = get_duck()
        base_uri = r2_uri(period_key("metadata"))
        for name, df in [
            ("events", events_df),
            ("markets", markets_df),
            ("outcome_tokens", tokens_df),
        ]:
            uri = f"{base_uri}/{name}.parquet"
            con.register("_export_df", df)
            con.execute(
                f"COPY _export_df TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true);"
            )
            con.unregister("_export_df")
            dbg.log("export", f"r2_{name}", uri)

        dbg.log("meta", "duckdb_version", con.execute("SELECT version()").fetchone()[0])


if __name__ == "__main__":
    main()

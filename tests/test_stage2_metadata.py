"""Offline tests for Stage 2 metadata/HTTP hygiene (no network, no R2)."""

from __future__ import annotations

import httpx
import pytest

from src.data.load_markets import build_tables_from_markets, fetch_keyset_pages
from src.data.stage2_metadata import decode_order_filled_token_id
from src.lib.checkpoint import Manifest, PageStore
from src.lib.http import PermanentHTTPError, PoliteClient


def _client(handler, sleeps):
    return PoliteClient(
        "https://example.test", min_interval_s=0.0, max_retries=3, backoff_base_s=0.01,
        transport=httpx.MockTransport(handler), sleep=sleeps.append,
    )


def test_retries_transient_then_succeeds_and_honours_retry_after():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        if calls["n"] == 2:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    out = _client(handler, sleeps).get_json("x")
    assert out == {"ok": True}
    assert calls["n"] == 3
    assert sleeps[0] >= 2.0


def test_permanent_4xx_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(422, json={"error": "bad"})

    with pytest.raises(PermanentHTTPError):
        _client(handler, []).get_json("x")
    assert calls["n"] == 1


def test_gives_up_after_bounded_retries():
    def handler(request):
        return httpx.Response(503)

    client = _client(handler, [])
    with pytest.raises(RuntimeError, match="Giving up"):
        client.get_json("x")
    assert client.stats.requests == 4


def test_keyset_pages_checkpoint_and_resume(tmp_path):
    pages = {None: {"events": [{"id": 1}], "next_cursor": "c2"}, "c2": {"events": [{"id": 2}]}}
    seen = []

    def handler(request):
        cur = request.url.params.get("after_cursor")
        seen.append(cur)
        return httpx.Response(200, json=pages[cur])

    store = PageStore(tmp_path)
    rows, st = fetch_keyset_pages(_client(handler, []), "events/keyset", {"limit": 1}, store, items_key="events")
    assert [r["id"] for r in rows] == [1, 2] and st["pages_fetched"] == 2
    rows2, st2 = fetch_keyset_pages(_client(handler, []), "events/keyset", {"limit": 1}, store, items_key="events")
    assert rows2 == rows and st2 == {"pages_fetched": 0, "pages_reused": 2, "rows": 2}
    assert len(seen) == 2


def test_manifest_atomic_roundtrip(tmp_path):
    m = Manifest(tmp_path / "m.json")
    m.set("slices", [{"index": 0, "status": "verified"}])
    assert Manifest(tmp_path / "m.json").get("slices")[0]["status"] == "verified"
    assert not list(tmp_path.glob(".*tmp"))


def test_decode_v2_token_id():
    token = 115150816097107968141822261120253656578439584638162037521124274735666572282165
    data = "0x" + f"{1:064x}" + f"{token:064x}" + "0" * 64 * 5
    assert decode_order_filled_token_id({"data": data}) == str(token)


def test_open_markets_have_null_payout_closed_have_winner():
    base = {"clobTokenIds": '["a","b"]', "outcomes": '["Yes","No"]', "events": [{"id": 9}]}
    open_m = {**base, "id": 1, "closed": False, "outcomePrices": '["0.97","0.03"]'}
    closed_m = {**base, "id": 2, "closed": True, "outcomePrices": '["0","1"]',
                "clobTokenIds": '["c","d"]'}
    _, markets, tokens = build_tables_from_markets([open_m, closed_m])
    pay = dict(zip(tokens["token_id"], tokens["resolved_payout"]))
    assert pay["a"] != pay["a"] and pay["b"] != pay["b"]
    assert pay["c"] == 0.0 and pay["d"] == 1.0
    assert markets.set_index("market_id").loc["1", "resolved_outcome_index"] != markets.set_index("market_id").loc["1", "resolved_outcome_index"]


def test_html_403_edge_block_is_retried_but_json_403_is_not():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, headers={"content-type": "text/html"}, text="<html>blocked</html>")
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    c = PoliteClient("https://x", min_interval_s=0, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert c.get_json("a") == {"ok": True}
    assert c.stats.edge_blocks == 1 and sleeps and sleeps[0] >= 60

    deny = PoliteClient(
        "https://x", min_interval_s=0, sleep=lambda s: None,
        transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "forbidden"})),
    )
    with pytest.raises(PermanentHTTPError):
        deny.get_json("a")


def test_windowed_events_split_on_persistent_edge_block(tmp_path, monkeypatch):
    from datetime import datetime

    from src.data import stage2_metadata

    monkeypatch.setattr(stage2_metadata, "_progress", lambda msg: None)

    def handler(request):
        lo = datetime.fromisoformat(request.url.params["end_date_min"].replace("Z", "+00:00")).timestamp()
        hi = datetime.fromisoformat(request.url.params["end_date_max"].replace("Z", "+00:00")).timestamp()
        if hi - lo > 3600:
            return httpx.Response(403, headers={"content-type": "text/html"}, text="<html>waf</html>")
        return httpx.Response(200, json={"events": [{"id": str(int(lo))}]})

    c = PoliteClient("https://x", min_interval_s=0, sleep=lambda s: None, transport=httpx.MockTransport(handler))
    rows, st = stage2_metadata.fetch_events_windowed(c, {"tag_slug": "5M"}, PageStore(tmp_path), 0, 4 * 3600)
    assert len(rows) == 4 and st["splits"] >= 1


def test_metadata_lookup_bisects_around_a_bad_token(tmp_path, monkeypatch):
    from src.data import stage2_metadata

    monkeypatch.setattr(stage2_metadata, "_progress", lambda msg: None)
    monkeypatch.setattr(stage2_metadata, "local_dir", lambda *p: tmp_path.joinpath(*p))

    def handler(request):
        ids = request.url.params.get_list("clob_token_ids")
        if "13" in ids:
            return httpx.Response(500)
        return httpx.Response(200, json={"markets": [{"id": t, "clobTokenIds": [t]} for t in ids]})

    real = stage2_metadata.gamma_client
    monkeypatch.setattr(
        stage2_metadata,
        "gamma_client",
        lambda base: PoliteClient(base, min_interval_s=0, max_retries=0, sleep=lambda s: None,
                                  transport=httpx.MockTransport(handler)),
    )
    toks = [str(i) for i in range(10, 30)]
    st = stage2_metadata.fetch_metadata_for_tokens(toks, batch=20)
    assert st["tokens_missing"] == 1 and st.get("tokens_error", 0) >= 1
    assert real is not None

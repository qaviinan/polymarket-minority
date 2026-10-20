"""Smoke tests: imports with dummy env (no R2 network required for import)."""

from __future__ import annotations

import os


def test_imports_with_dummy_r2():
    os.environ.setdefault("R2_BUCKET", "test-bucket")
    os.environ.setdefault("R2_ACCOUNT_ID", "0" * 32)
    os.environ.setdefault("R2_ACCESS_KEY_ID", "x")
    os.environ.setdefault("R2_SECRET_ACCESS_KEY", "y")
    from src.lib import duck, debug, provenance
    from src.analysis import (
        classification,
        composition_audit,
        coverage_audit,
        event_grouping,
        event_split_persistence,
        event_split_robustness,
        favorite_buying,
        market_closure,
        multiple_testing,
        oib_regression,
        partition,
        pnl,
        population_audit,
        sign_construct_audit,
        sign_randomization,
        stage1_gate,
        stage2_compare,
        trader_sanity,
    )
    from src.data import load_markets, stage2_metadata, verify_ingest
    from src.lib import polygon

    assert duck.r2_uri("a", "b") == "r2://test-bucket/a/b"
    os.environ["PERIOD_ID"] = "recent_2026"
    os.environ.pop("LOCAL_R2_MIRROR", None)
    try:
        assert duck.r2_uri("order_filled", "**", "*.parquet") == (
            "r2://test-bucket/periods/recent_2026/order_filled/**/*.parquet"
        )
        assert duck.r2_uri("periods", "recent_2026", "metadata") == (
            "r2://test-bucket/periods/recent_2026/metadata"
        )
        from src.lib.period import logs_dir, period_key

        assert "periods/recent_2026" in str(logs_dir())
        assert duck.r2_uri(period_key("metadata", "events.parquet")) == (
            "r2://test-bucket/periods/recent_2026/metadata/events.parquet"
        )
        os.environ["LOCAL_R2_MIRROR"] = "/tmp/pm_mirror"
        assert duck.r2_uri("order_filled", "**", "*.parquet") == (
            "/tmp/pm_mirror/order_filled/**/*.parquet"
        )
        os.environ.pop("LOCAL_R2_MIRROR", None)
    finally:
        os.environ.pop("PERIOD_ID", None)
        os.environ.pop("LOCAL_R2_MIRROR", None)
    assert debug.DebugReport is not None
    assert provenance.manifest_id({"a": 1}) == provenance.manifest_id({"a": 1})

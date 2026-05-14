"""Smoke tests: imports with dummy env (no R2 network required for import)."""

from __future__ import annotations

import os


def test_imports_with_dummy_r2():
    os.environ.setdefault("R2_BUCKET", "test-bucket")
    os.environ.setdefault("R2_ACCOUNT_ID", "0" * 32)
    os.environ.setdefault("R2_ACCESS_KEY_ID", "x")
    os.environ.setdefault("R2_SECRET_ACCESS_KEY", "y")
    from src.lib import duck, debug
    from src.analysis import event_grouping, partition, pnl, sign_randomization, classification, oib_regression
    from src.data import load_markets, render_pipeline_yaml, verify_ingest, wait_for_backfill

    assert duck.r2_uri("a", "b") == "r2://test-bucket/a/b"
    assert debug.DebugReport is not None

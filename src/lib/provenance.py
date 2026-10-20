"""Small, serializable manifests that bind analysis outputs to their inputs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def manifest_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def build_manifest(
    con,
    *,
    step: str,
    source_view: str,
    label_scope: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = con.execute(
        f"""
        SELECT COUNT(*) AS n_rows,
               MIN(CAST(block_timestamp AS BIGINT)) AS ts_min,
               MAX(CAST(block_timestamp AS BIGINT)) AS ts_max
        FROM {source_view}
        """
    ).fetchone()
    payload: dict[str, Any] = {
        "step": step,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": _git_revision(),
        "source_view": source_view,
        "source_rows": int(row[0] or 0),
        "source_ts_min": int(row[1]) if row[1] is not None else None,
        "source_ts_max": int(row[2]) if row[2] is not None else None,
        "label_scope": label_scope,
        "analysis_use_last_n_days": os.environ.get("ANALYSIS_USE_LAST_N_DAYS"),
        "analysis_block_ts_min": os.environ.get("ANALYSIS_BLOCK_TS_MIN"),
        "analysis_block_ts_max": os.environ.get("ANALYSIS_BLOCK_TS_MAX"),
        "analysis_week_anchor_ts": os.environ.get("ANALYSIS_WEEK_ANCHOR_TS"),
        "minority_label_split": os.environ.get("MINORITY_LABEL_SPLIT"),
        "period_id": os.environ.get("PERIOD_ID"),
    }
    payload.update(extra or {})
    payload["manifest_id"] = manifest_id(payload)
    return payload


def save_manifest(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path

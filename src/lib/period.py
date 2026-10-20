"""Period scoping for R2 prefixes and local checkpoints.

``PERIOD_ID`` unset keeps the Stage 1 layout (``metadata/``, ``order_filled/``) so 2024
artifacts are never overwritten. Any other period lives under ``periods/<PERIOD_ID>/``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_PERIOD_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def period_id() -> str:
    pid = os.environ.get("PERIOD_ID", "").strip().lower()
    if pid and not _PERIOD_RE.match(pid):
        raise RuntimeError(f"PERIOD_ID={pid!r} must match {_PERIOD_RE.pattern}")
    return pid


def period_key(*parts: str) -> str:
    """R2 key (no bucket) for ``parts`` inside the active period."""
    pid = period_id()
    segs = (["periods", pid] if pid else []) + [p.strip("/") for p in parts if p]
    return "/".join(segs)


def local_dir(*parts: str) -> Path:
    """Local checkpoint directory for the active period (gitignored under ``data/``)."""
    pid = period_id() or "stage1_2024"
    path = Path("data") / "periods" / pid
    for p in parts:
        path = path / p
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """JSONL + diagnostic root. Period-scoped so Stage 2 never overwrites Stage 1 logs."""
    pid = period_id()
    path = Path("logs") / "periods" / pid if pid else Path("logs")
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_path(*parts: str) -> Path:
    path = logs_dir()
    for p in parts:
        path = path / p
    return path

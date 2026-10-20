"""Crash-safe local checkpoints: atomic gzip-JSON pages plus a JSON manifest.

Every write goes to a temporary file in the same directory and is renamed into place,
so an interrupted run leaves either the previous file or the complete new one.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def stable_key(*parts: Any) -> str:
    """Deterministic short key for a request (endpoint + params)."""
    blob = json.dumps(parts, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:20]


class PageStore:
    """Directory of gzip JSON pages keyed by name; existing pages are reused, not refetched."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json.gz"

    def has(self, key: str) -> bool:
        return self.path(key).exists()

    def load(self, key: str) -> Any:
        with gzip.open(self.path(key), "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, key: str, obj: Any) -> None:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        _atomic_write_bytes(self.path(key), gzip.compress(raw, compresslevel=6))

    def keys(self) -> list[str]:
        return sorted(p.name.removesuffix(".json.gz") for p in self.root.glob("*.json.gz"))


class Manifest:
    """Small JSON document of run state; every update is persisted atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self) -> None:
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _atomic_write_bytes(self.path, json.dumps(self.data, indent=2, sort_keys=True).encode())

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

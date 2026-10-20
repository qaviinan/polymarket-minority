"""Goldsky is retired: no code path may call its API/CLI or read its credentials."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(
    r"GOLDSKY_|(?i:goldsky\.com|/\.goldsky|['\"](?:goldsky|turbo)['\"]|\bturbo\s+(?:apply|get|logs|validate|delete)\b)"
)
CODE_GLOBS = ("src/**/*.py", "scripts/**/*.py", "logs/*.sh", "notebooks/*.ipynb", "tests/**/*.py")


def test_no_goldsky_call_sites():
    hits = []
    for pattern in CODE_GLOBS:
        for path in ROOT.glob(pattern):
            if path.resolve() == Path(__file__).resolve():
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if FORBIDDEN.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not hits, "Goldsky references found:\n" + "\n".join(hits)


def test_no_goldsky_credentials_in_env_files():
    for name in (".env", ".env.example"):
        path = ROOT / name
        if path.exists():
            keys = [ln.split("=", 1)[0] for ln in path.read_text().splitlines() if "=" in ln]
            assert not [k for k in keys if "GOLDSKY" in k.upper()], f"{name} still defines Goldsky keys"


def test_no_goldsky_pipeline_definitions():
    assert not (ROOT / "goldsky").exists()
    assert not list(ROOT.glob("data/periods/*/pipelines/*.yaml"))

"""Structured debug logging + Rich summaries for pipeline steps."""

from __future__ import annotations

import json
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

AssertionLevel = Literal["green", "yellow", "red"]


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class DebugReport:
    """Per-step JSONL log + Rich table summary + optional diagnostic Parquet."""

    step_name: str
    logs_dir: Path = field(default_factory=lambda: Path("logs"))
    _events: list[dict[str, Any]] = field(default_factory=list)
    _assertions: list[dict[str, Any]] = field(default_factory=list)
    _start: float = field(default_factory=time.perf_counter)
    _jsonl_fh: Any = None

    def __post_init__(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = self.logs_dir / f"{self.step_name}_{_utc_ts()}.jsonl"
        self._diag_dir = self.logs_dir / self.step_name
        self._diag_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_fh = open(self._jsonl_path, "a", encoding="utf-8")

    def _write(self, row: dict[str, Any]) -> None:
        self._jsonl_fh.write(json.dumps(row, default=str) + "\n")
        self._jsonl_fh.flush()

    def log(
        self,
        phase: str,
        metric: str,
        value: Any,
        notes: str | None = None,
        **extra: Any,
    ) -> None:
        row = {
            "step": self.step_name,
            "phase": phase,
            "metric": metric,
            "value": value,
            "notes": notes,
            **extra,
        }
        self._events.append(row)
        self._write(row)

    def assertion(
        self,
        name: str,
        level: AssertionLevel,
        detail: str,
        metric: str | None = None,
        value: Any = None,
    ) -> None:
        a = {
            "step": self.step_name,
            "phase": "assertion",
            "name": name,
            "level": level,
            "detail": detail,
            "metric": metric,
            "value": value,
        }
        self._assertions.append(a)
        self._write(a)

    def save_diagnostic_df(self, name: str, df: Any) -> Path:
        """Save pandas DataFrame to logs/<step>/<name>.parquet."""
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            raise TypeError("save_diagnostic_df expects pandas.DataFrame")
        path = self._diag_dir / f"{name}.parquet"
        df.to_parquet(path, index=False)
        self.log("diagnostic", "saved_parquet", str(path), table=name)
        return path

    def save_diagnostic_json(self, name: str, obj: Any) -> Path:
        path = self._diag_dir / f"{name}.json"
        path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        self.log("diagnostic", "saved_json", str(path), table=name)
        return path

    def close(self) -> None:
        if self._jsonl_fh and not self._jsonl_fh.closed:
            self._jsonl_fh.close()
            self._jsonl_fh = None

    def elapsed_s(self) -> float:
        return time.perf_counter() - self._start

    def finish_console_summary(self) -> None:
        """Render Rich summary to stdout."""
        try:
            from rich.console import Console
            from rich.table import Table

            console = Console(file=sys.stdout)
            console.rule(f"[bold]{self.step_name}[/] debug summary")
            t = Table(show_header=True, header_style="bold")
            t.add_column("phase")
            t.add_column("metric")
            t.add_column("value")
            t.add_column("notes")
            for e in self._events[-80:]:
                if e.get("phase") == "assertion":
                    continue
                t.add_row(
                    str(e.get("phase", "")),
                    str(e.get("metric", "")),
                    str(e.get("value", ""))[:200],
                    str(e.get("notes", "") or "")[:120],
                )
            console.print(t)
            if self._assertions:
                ta = Table(title="Assertions (soft checks)", show_header=True)
                ta.add_column("level", style="bold")
                ta.add_column("name")
                ta.add_column("detail")
                for a in self._assertions:
                    style = {"green": "green", "yellow": "yellow", "red": "red"}.get(
                        str(a.get("level")), "white"
                    )
                    ta.add_row(
                        f"[{style}]{a.get('level')}[/]",
                        str(a.get("name", "")),
                        str(a.get("detail", "")),
                    )
                console.print(ta)
            console.print(f"Elapsed: {self.elapsed_s():.2f}s | JSONL: {self._jsonl_path}")
        except Exception:
            print(f"[{self.step_name}] {len(self._events)} events -> {self._jsonl_path}")
            for a in self._assertions:
                print(f"  [{a.get('level')}] {a.get('name')}: {a.get('detail')}")


@contextmanager
def debug_step(step_name: str, logs_dir: Path | None = None) -> Iterator[DebugReport]:
    rep = DebugReport(step_name=step_name, logs_dir=logs_dir or Path("logs"))
    try:
        yield rep
    except Exception as e:
        rep.log("error", "exception", repr(e), notes=traceback.format_exc())
        rep.assertion("step_failed", "red", str(e))
        raise
    finally:
        rep.log("meta", "elapsed_seconds", rep.elapsed_s())
        rep.finish_console_summary()
        rep.close()

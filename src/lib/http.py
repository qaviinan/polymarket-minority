"""Polite HTTP / JSON-RPC clients: rate limiting, bounded retries, backoff with jitter.

Retries cover timeouts, connection errors, HTTP 429 and 5xx. ``Retry-After`` is honoured.
Other 4xx responses fail immediately because retrying a bad request only adds load.
Request logs record paths and parameter names only, never values that could hold keys.
"""

from __future__ import annotations

import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}


class PermanentHTTPError(RuntimeError):
    pass


class EdgeBlockedError(PermanentHTTPError):
    """CDN/WAF 403 that persists across retries (e.g. a keyset cursor matching a WAF rule)."""


@dataclass
class RequestStats:
    requests: int = 0
    retries: int = 0
    failures: int = 0
    bytes_received: int = 0
    sleep_s: float = 0.0
    by_status: dict[int, int] = field(default_factory=dict)
    edge_blocks: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "retries": self.retries,
            "edge_blocks": self.edge_blocks,
            "failures": self.failures,
            "bytes_received": self.bytes_received,
            "sleep_s": round(self.sleep_s, 2),
            "by_status": {str(k): v for k, v in sorted(self.by_status.items())},
        }


class _RateLimiter:
    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval_s
        if delay:
            time.sleep(delay)
        return delay


def backoff_delay(attempt: int, base_s: float, cap_s: float, rng: random.Random) -> float:
    """Full-jitter exponential backoff: uniform(0, min(cap, base * 2**attempt))."""
    return rng.uniform(0.0, min(cap_s, base_s * (2**attempt)))


def _retry_after_s(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class PoliteClient:
    """GET JSON with a per-host minimum interval and bounded, jittered retries."""

    def __init__(
        self,
        base_url: str,
        *,
        min_interval_s: float = 0.5,
        max_retries: int = 6,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 60.0,
        timeout_s: float = 60.0,
        headers: dict[str, str] | None = None,
        seed: int = 0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        edge_block_cap_s: float = 900.0,
        edge_block_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.edge_block_cap_s = edge_block_cap_s
        self.edge_block_retries = edge_block_retries
        self._edge_seen = 0
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self.stats = RequestStats()
        self._limiter = _RateLimiter(min_interval_s)
        self._rng = random.Random(seed)
        self._sleep = sleep
        hdrs = {"User-Agent": "polymarket-research/stage2 (polite batch client)"}
        hdrs.update(headers or {})
        self._client = httpx.Client(timeout=timeout_s, headers=hdrs, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _pause(self, seconds: float) -> None:
        self.stats.sleep_s += seconds
        self._sleep(seconds)

    def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.stats.sleep_s += self._limiter.wait()
            self.stats.requests += 1
            try:
                resp = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = exc
                status = None
            else:
                status = resp.status_code
                self.stats.by_status[status] = self.stats.by_status.get(status, 0) + 1
                self.stats.bytes_received += len(resp.content)
                if status < 400:
                    self._edge_seen = 0
                    return resp.json()
                edge_block = status == 403 and "html" in resp.headers.get("content-type", "").lower()
                if status not in RETRY_STATUS and not edge_block:
                    self.stats.failures += 1
                    raise PermanentHTTPError(f"HTTP {status} for {method} {path}: {resp.text[:200]}")
                last_err = RuntimeError(f"HTTP {status}")
                if edge_block:
                    self.stats.edge_blocks += 1
                    edge_seen = getattr(self, "_edge_seen", 0) + 1
                    self._edge_seen = edge_seen
                    if edge_seen > self.edge_block_retries:
                        self._edge_seen = 0
                        self.stats.failures += 1
                        raise EdgeBlockedError(f"HTTP 403 edge block for {method} {path} (persistent)")
                    if attempt < self.max_retries:
                        self.stats.retries += 1
                        wait = min(self.edge_block_cap_s, 60.0 * 2**attempt)
                        print(f"[http] 403 edge block on {path}; waiting {wait:.0f}s", file=sys.stderr, flush=True)
                        self._pause(wait)
                        continue
            if attempt == self.max_retries:
                break
            self.stats.retries += 1
            delay = backoff_delay(attempt, self.backoff_base_s, self.backoff_cap_s, self._rng)
            if status is not None:
                hinted = _retry_after_s(resp)
                if hinted is not None:
                    delay = max(delay, min(hinted, self.backoff_cap_s * 5))
            print(f"[http] {last_err!r} on {path}; retry {attempt + 1} in {delay:.1f}s", file=sys.stderr, flush=True)
            self._pause(delay)
        self.stats.failures += 1
        raise RuntimeError(f"Giving up on {method} {path} after {self.max_retries + 1} attempts: {last_err!r}")

    def get_json(self, path: str, params: Any = None) -> Any:
        return self.request_json("GET", path, params=params)


class JsonRpc:
    """JSON-RPC over several endpoints; each endpoint is a PoliteClient, failover on exhaustion."""

    def __init__(self, urls: list[str], *, min_interval_s: float = 0.25, max_retries: int = 4) -> None:
        if not urls:
            raise RuntimeError("JsonRpc needs at least one URL")
        self.clients = [
            PoliteClient(u, min_interval_s=min_interval_s, max_retries=max_retries, timeout_s=60.0)
            for u in urls
        ]
        self._preferred = 0

    def call(self, method: str, params: list) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        order = self.clients[self._preferred:] + self.clients[: self._preferred]
        last_err: Exception | None = None
        for client in order:
            try:
                out = client.request_json("POST", client.base_url, json=payload)
            except Exception as exc:
                last_err = exc
                continue
            if isinstance(out, dict) and "error" in out:
                last_err = RuntimeError(str(out["error"])[:300])
                continue
            self._preferred = self.clients.index(client)
            return out["result"]
        raise RuntimeError(f"All RPC endpoints failed for {method}: {last_err!r}")

    def stats(self) -> dict[str, Any]:
        return {c.base_url.split("//")[-1].split("/")[0]: c.stats.as_dict() for c in self.clients}

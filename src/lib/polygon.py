"""Polygon JSON-RPC helpers: endpoint failover and block/timestamp lookups."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from src.lib.http import JsonRpc

# Public Polygon RPCs (no API key). Tried after POLYGON_RPC_URL and POLYGON_RPC_FALLBACK_URLS.
_DEFAULT_POLYGON_RPC_FALLBACKS = (
    "https://polygon-bor.publicnode.com",
    "https://1rpc.io/matic",
    "https://polygon.drpc.org",
)

_rpc: JsonRpc | None = None
_block_ts_cache: dict[int, int] = {}
_POLYGON_SECONDS_PER_BLOCK_GUESS = 2.0


def _polygon_rpc_candidates() -> list[str]:
    """Ordered list of JSON-RPC URLs to try (deduped)."""
    out: list[str] = []
    primary = os.environ.get("POLYGON_RPC_URL", "").strip()
    strict = os.environ.get("POLYGON_RPC_STRICT", "").lower() in ("1", "true", "yes")
    if primary:
        out.append(primary)
    if strict:
        if not out:
            raise RuntimeError("POLYGON_RPC_STRICT is set but POLYGON_RPC_URL is empty")
        return out
    for part in os.environ.get("POLYGON_RPC_FALLBACK_URLS", "").split(","):
        s = part.strip()
        if s and s not in out:
            out.append(s)
    for d in _DEFAULT_POLYGON_RPC_FALLBACKS:
        if d not in out:
            out.append(d)
    return out or ["https://polygon-bor.publicnode.com"]


def polygon_rpc() -> JsonRpc:
    """Shared polite JSON-RPC client (rate-limited, retried, endpoint failover)."""
    global _rpc
    if _rpc is None:
        _rpc = JsonRpc(_polygon_rpc_candidates())
    return _rpc


def _eth_rpc(method: str, params: list) -> dict:
    try:
        return polygon_rpc().call(method, params)
    except RuntimeError as e:
        hosts = ",".join(urlparse(u).netloc or u for u in _polygon_rpc_candidates())
        raise RuntimeError(
            "All Polygon RPC endpoints failed (often 401 on https://polygon-rpc.com without an API key). "
            f"Tried hosts: {hosts}. Set POLYGON_RPC_URL to a provider you trust "
            "(e.g. Alchemy / Infura / Ankr with your key), or add POLYGON_RPC_FALLBACK_URLS. "
            f"Last error: {e}"
        ) from e


def _hex_int(x: str) -> int:
    return int(x, 16)


def latest_block(safety: int = 64) -> int:
    """Chain head minus ``safety`` blocks, so failover nodes that lag slightly still have it."""
    return _hex_int(_eth_rpc("eth_blockNumber", [])) - safety


def block_timestamp(block_number: int) -> int:
    if block_number in _block_ts_cache:
        return _block_ts_cache[block_number]
    blk = _eth_rpc("eth_getBlockByNumber", [hex(block_number), False])
    if not blk or "timestamp" not in blk:
        raise RuntimeError(f"No block {block_number}")
    _block_ts_cache[block_number] = _hex_int(blk["timestamp"])
    return _block_ts_cache[block_number]


def default_search_bounds(target_ts: int) -> tuple[int, int]:
    """Search window around ``target_ts`` from the chain head (works for any era)."""
    head = latest_block()
    head_ts = block_timestamp(head)
    est = head - int(max(0, head_ts - target_ts) / _POLYGON_SECONDS_PER_BLOCK_GUESS)
    margin = 200_000 + abs(head - est) // 10
    return max(1, est - margin), min(head, est + margin)


def find_block_at_or_before(target_ts: int, lo: int, hi: int) -> int:
    """Largest block in [lo, hi] with timestamp <= target_ts."""
    while block_timestamp(lo) > target_ts and lo > 1:
        lo = max(1, lo - 500_000)
    if block_timestamp(lo) > target_ts:
        raise RuntimeError(f"lo={lo} already after target; widen search range downward")
    head = latest_block()
    while block_timestamp(hi) < target_ts and hi < head:
        hi = min(head, hi + 500_000)
    if block_timestamp(hi) < target_ts:
        return hi

    ans = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        ts = block_timestamp(mid)
        if ts <= target_ts:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans

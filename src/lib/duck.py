"""DuckDB connection with Cloudflare R2 httpfs secret."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    import duckdb


def load_env() -> None:
    """Load `.env` from the repo root (next to `pyproject.toml`), then cwd (override)."""
    from pathlib import Path

    here = Path(__file__).resolve().parent
    repo_root = here.parent.parent
    env_repo = repo_root / ".env"
    if env_repo.is_file():
        load_dotenv(env_repo)
    load_dotenv()


def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def r2_bucket() -> str:
    return require_env("R2_BUCKET")


def r2_uri(*parts: str) -> str:
    """Build r2://bucket/key segments (no leading slash on key).

    When ``PERIOD_ID`` is set, keys are placed under ``periods/<id>/`` unless ``parts``
    already start with ``periods`` (as keys from ``period_key`` do).
    ``LOCAL_R2_MIRROR`` rewrites keys to a local directory (after stripping ``periods/<id>/``).
    """
    from src.lib.period import period_id

    segs = [p.strip("/") for p in parts if p]
    pid = period_id()
    already_period = bool(segs) and (
        segs[0] == "periods" or segs[0].startswith("periods/")
    )
    if pid and not already_period:
        segs = ["periods", pid, *segs]
    key = "/".join(segs)
    mirror = os.environ.get("LOCAL_R2_MIRROR", "").strip()
    if mirror:
        rel = key
        if pid and rel.startswith(f"periods/{pid}/"):
            rel = rel[len(f"periods/{pid}/") :]
        path = Path(mirror).joinpath(rel)
        if "*" not in rel:
            path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)
    return f"r2://{r2_bucket()}/{key}"


def get_duck() -> "duckdb.DuckDBPyConnection":
    import duckdb

    load_env()
    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(
        "SET http_retries=10; SET http_retry_wait_ms=2000; SET http_retry_backoff=2; SET http_timeout=120; "
        "SET enable_http_metadata_cache=true; SET parquet_metadata_cache=true;"
    )
    account = require_env("R2_ACCOUNT_ID")
    key_id = require_env("R2_ACCESS_KEY_ID")
    secret = require_env("R2_SECRET_ACCESS_KEY")
    region = os.environ.get("R2_REGION", "auto")
    try:
        con.execute("DROP SECRET IF EXISTS r2_secret;")
    except Exception:
        pass
    con.execute(
        f"""
        CREATE SECRET r2_secret (
            TYPE R2,
            KEY_ID '{key_id.replace("'", "''")}',
            SECRET '{secret.replace("'", "''")}',
            ACCOUNT_ID '{account.replace("'", "''")}',
            REGION '{region.replace("'", "''")}'
        );
        """
    )
    return con


def healthcheck() -> None:
    """Write and read a tiny Parquet on R2 to validate credentials."""
    con = get_duck()
    uri = r2_uri("healthcheck", "probe.parquet")
    con.execute(
        f"""
        COPY (SELECT i AS id FROM range(100) t(i))
        TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true);
        """
    )
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{uri}')").fetchone()[0]
    print(f"healthcheck OK: wrote/read {n} rows at {uri}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DuckDB + R2 helpers")
    parser.add_argument("--healthcheck", action="store_true", help="R2 read/write probe")
    args = parser.parse_args()
    if args.healthcheck:
        healthcheck()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

"""DuckDB connection with Cloudflare R2 httpfs secret."""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    import duckdb


def load_env() -> None:
    load_dotenv()


def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def r2_bucket() -> str:
    return require_env("R2_BUCKET")


def r2_uri(*parts: str) -> str:
    """Build r2://bucket/key segments (no leading slash on key)."""
    key = "/".join(p.strip("/") for p in parts if p)
    return f"r2://{r2_bucket()}/{key}"


def get_duck() -> "duckdb.DuckDBPyConnection":
    import duckdb

    load_env()
    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    account = require_env("R2_ACCOUNT_ID")
    key_id = require_env("R2_ACCESS_KEY_ID")
    secret = require_env("R2_SECRET_ACCESS_KEY")
    region = os.environ.get("R2_REGION", "auto")
    # Drop existing secret if re-running in same process
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

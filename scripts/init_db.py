"""Database initialization script.

Creates PostgreSQL and ClickHouse tables for ArBot.
Uses schema SQL files from scripts/sql/ directory.

Usage:
    python scripts/init_db.py [--config-dir configs] [--postgres-only] [--clickhouse-only]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
from clickhouse_driver import Client as ClickHouseClient

# Add src to path so we can import arbot.config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from arbot.config import ClickHouseConfig, PostgresConfig, load_config

SQL_DIR = Path(__file__).resolve().parent / "sql"


async def init_postgres(config: PostgresConfig) -> None:
    """Initialize PostgreSQL schema.

    Args:
        config: PostgreSQL connection configuration.
    """
    print(f"Connecting to PostgreSQL at {config.host}:{config.port}/{config.database}...")

    # First connect to default 'postgres' database to ensure target database exists
    try:
        sys_conn = await asyncpg.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database="postgres",
        )
        db_exists = await sys_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", config.database
        )
        if not db_exists:
            await sys_conn.execute(f'CREATE DATABASE "{config.database}"')
            print(f"Created database '{config.database}'")
        await sys_conn.close()
    except Exception as e:
        print(f"Warning: Could not check/create database: {e}")
        print("Assuming database already exists, proceeding with schema creation...")

    # Connect to the target database and apply schema
    conn = await asyncpg.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
    )

    try:
        schema_sql = (SQL_DIR / "postgres_schema.sql").read_text()
        await conn.execute(schema_sql)
        print("PostgreSQL schema initialized successfully.")
    finally:
        await conn.close()


def init_clickhouse(config: ClickHouseConfig) -> None:
    """Initialize ClickHouse schema.

    Args:
        config: ClickHouse connection configuration.
    """
    print(f"Connecting to ClickHouse at {config.host}:{config.port}...")

    client = ClickHouseClient(
        host=config.host,
        port=config.port,
    )

    # Create database if not exists
    client.execute(f"CREATE DATABASE IF NOT EXISTS {config.database}")
    print(f"Ensured database '{config.database}' exists.")

    # Read and execute schema statements one by one
    schema_sql = (SQL_DIR / "clickhouse_schema.sql").read_text()

    # Split on semicolons, filter out empty/comment-only statements
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    for stmt in statements:
        # Skip comment-only blocks
        lines = [line for line in stmt.splitlines() if not line.strip().startswith("--")]
        if not "".join(lines).strip():
            continue
        client.execute(f"USE {config.database}")
        client.execute(stmt)

    print("ClickHouse schema initialized successfully.")


async def main() -> None:
    """Run database initialization."""
    parser = argparse.ArgumentParser(description="Initialize ArBot databases")
    parser.add_argument(
        "--config-dir",
        default="configs",
        help="Path to configuration directory (default: configs)",
    )
    parser.add_argument(
        "--postgres-only",
        action="store_true",
        help="Only initialize PostgreSQL",
    )
    parser.add_argument(
        "--clickhouse-only",
        action="store_true",
        help="Only initialize ClickHouse",
    )
    args = parser.parse_args()

    config = load_config(config_dir=args.config_dir)
    db_config = config.database

    init_pg = not args.clickhouse_only
    init_ch = not args.postgres_only

    if init_pg:
        await init_postgres(db_config.postgres)

    if init_ch:
        init_clickhouse(db_config.clickhouse)

    print("Database initialization complete.")


if __name__ == "__main__":
    asyncio.run(main())

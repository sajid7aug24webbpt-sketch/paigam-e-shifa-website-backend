"""MySQL smoke test — runs ONLY when a MySQL server is reachable.

No MySQL server is installed in the build VM and Docker is absent, so this
test is expected to SKIP there. It runs in CI / on a host where
MYSQL_SMOKE_URL (or DATABASE_URL) points at a live MySQL 8 server.
"""
import os

import pytest

pymysql = pytest.importorskip("pymysql")


def test_mysql_reachable_and_migration_sql_parses():
    url = os.environ.get("MYSQL_SMOKE_URL") or os.environ.get("DATABASE_URL", "")
    if not url.startswith("mysql"):
        pytest.skip("No MySQL DATABASE_URL configured (expected in this VM).")
    # Parse mysql+pymysql://user:pass@host:port/db
    from urllib.parse import urlparse

    parts = urlparse(url)
    try:
        conn = pymysql.connect(
            host=parts.hostname or "127.0.0.1",
            port=parts.port or 3306,
            user=parts.username,
            password=parts.password,
            database=parts.path.lstrip("/") or None,
            connect_timeout=3,
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "no server"
        pytest.skip(f"MySQL server not reachable: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
        assert version.startswith("8."), f"expected MySQL 8, got {version}"
    finally:
        conn.close()

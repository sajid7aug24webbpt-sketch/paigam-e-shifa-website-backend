"""Apply backend/migrations/*.sql in filename order, idempotently.

Tracks applied versions in the `schema_migrations` table. MySQL is the
deploy target; SQLite is NOT supported here (its dialect differs) — for
local dev/tests use the app's db.create_all() via scripts/seed.py instead.

Usage:
    DATABASE_URL=mysql+pymysql://user:pass@host:3306/paigam_shifa \
        python scripts/migrate.py
    # or, from the backend/ directory with a .env file present:
    python scripts/migrate.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(1)
    if database_url.startswith("sqlite"):
        print("ERROR: migrate.py targets MySQL only. For SQLite dev/tests, "
              "use scripts/seed.py (db.create_all) instead.")
        sys.exit(1)

    engine = create_engine(database_url)
    files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))
    if not files:
        print("No migrations found.")
        return

    with engine.begin() as conn:
        # schema_migrations table itself (created by 0001 too; ensure it exists first).
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(64) NOT NULL PRIMARY KEY, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        applied = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))
        }
        for filename in files:
            version = filename[:-4]  # strip ".sql"
            if version in applied:
                print(f"SKIP {filename} (already applied)")
                continue
            sql = open(os.path.join(MIGRATIONS_DIR, filename), encoding="utf-8").read()
            # Split on ";" — this migration set has no stored procedures,
            # so a plain split is safe.
            statements = [s.strip() for s in sql.split(";") if s.strip()]
            for stmt in statements:
                conn.execute(text(stmt))
            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                {"v": version},
            )
            print(f"APPLIED {filename}")
    print("Migrations complete.")


if __name__ == "__main__":
    main()

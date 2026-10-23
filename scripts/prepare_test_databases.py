"""Explicit, non-destructive schema setup for dedicated test DBs, including fresh CI."""

import os
import subprocess

from psycopg import sql

from scripts.backup_restore import connection
from tests.database_support import database_settings
from tests.process_support import python_module

TEST_DATABASES = ("researchflow_test", "researchflow_agent_test", "researchflow_security_test")


def main():
    settings = database_settings("researchflow_test")
    if settings.environment != "test" or settings.database_host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Explicit test environment and isolated loopback PostgreSQL required")
    if settings.environment != "test":
        raise SystemExit("Explicit RESEARCHFLOW_ENVIRONMENT=test required")
    for name in TEST_DATABASES:
        with connection(settings, "postgres", autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        environment = {
            **os.environ,
            "RESEARCHFLOW_ENVIRONMENT": "test",
            "RESEARCHFLOW_DATABASE_NAME": name,
            "RESEARCHFLOW_DATABASE_HOST": settings.database_host,
            "RESEARCHFLOW_DATABASE_PORT": str(settings.database_port),
            "RESEARCHFLOW_DATABASE_USER": settings.database_user,
            "RESEARCHFLOW_DATABASE_PASSWORD": settings.database_password.get_secret_value(),
        }
        for module, arguments in (
            ("alembic", ["upgrade", "head"]),
            ("researchflow.cli", ["agent-setup"]),
        ):
            subprocess.run(python_module(module, *arguments), env=environment, check=True)
    print("Dedicated test schemas/checkpoints ready; no databases dropped or reset")


if __name__ == "__main__":
    main()

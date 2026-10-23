"""Explicit test identities: isolated DI for unit tests, real login for PostgreSQL tests."""

from functools import lru_cache
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from researchflow.auth.audit import audit_writer
from researchflow.auth.context import Principal, actor_scope
from researchflow.auth.dependencies import require_identity
from researchflow.auth.service import password_hasher
from tests.database_support import validate_test_database

TEST_USER_ID = UUID("73c13542-325b-492a-b59f-21767fdc37df")
TEST_USERNAME = "isolated-legacy-test-user"
TEST_PASSWORD = "Synthetic-only-not-for-real-accounts-2026"


@lru_cache(maxsize=1)
def test_password_hash():
    return password_hasher.hash(TEST_PASSWORD)


def bootstrap_test_identity(settings):
    validate_test_database(settings)
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password.get_secret_value(),
        dbname=settings.database_name,
    ) as connection:
        connection.execute(
            """INSERT INTO app_users(id, username, password_hash)
            VALUES(%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
            (TEST_USER_ID, TEST_USERNAME, test_password_hash()),
        )


class MemoryCallStore:
    def __init__(self):
        self.rows = {}

    async def start(self, row):
        row.id = uuid4()
        self.rows[row.id] = row

    async def finish(self, row):
        self.rows[row.id] = row


class UnitTestClient(TestClient):
    """No PostgreSQL in unit tests: authentication/storage are explicitly injected per app."""

    def __init__(self, app, *args, **kwargs):
        self.audit_events = []

        async def memory_audit(*args, **kwargs):
            self.audit_events.append((args, kwargs))

        async def unit_identity():
            token = audit_writer.set(memory_audit)
            try:
                with actor_scope(Principal(TEST_USER_ID)):
                    yield Principal(TEST_USER_ID)
            finally:
                audit_writer.reset(token)

        app.dependency_overrides[require_identity] = unit_identity
        app.state.llm_provider.call_store = MemoryCallStore()
        super().__init__(app, *args, **kwargs)


class AuthenticatedTestClient(TestClient):
    """Normal bearer validation remains enabled, including in worker subprocesses."""

    def __enter__(self):
        bootstrap_test_identity(self.app.state.settings)
        super().__enter__()
        logged = self.post(
            "/api/v1/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )
        assert logged.status_code == 200, logged.text
        self.headers["Authorization"] = "Bearer " + logged.json()["access_token"]
        self._actor_scope = actor_scope(Principal(TEST_USER_ID))
        self._actor_scope.__enter__()
        return self

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self._actor_scope.__exit__(*args)

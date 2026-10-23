"""Shared fixtures for isolated API tests."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from researchflow.auth.context import Principal, actor_scope
from researchflow.core.config import Settings
from researchflow.main import create_app
from tests.auth_support import TEST_USER_ID, UnitTestClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def explicit_library_test_actor():
    # Library-only tests have a named synthetic actor, not a global authentication-disable switch.
    # All real HTTP security tests use original TestClient and authenticate through the database.
    with actor_scope(Principal(TEST_USER_ID)):
        yield


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_password="unit-test-password",
        _env_file=None,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with UnitTestClient(create_app(settings=settings)) as test_client:
        yield test_client

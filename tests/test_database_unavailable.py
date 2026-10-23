"""Database failures are explicit without making liveness depend on PostgreSQL."""

import logging

import pytest

from researchflow.core.config import Settings
from researchflow.main import create_app
from tests.auth_support import UnitTestClient as TestClient


def test_database_down_changes_readiness_but_not_health(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        environment="test",
        database_host="127.0.0.1",
        database_port=1,
        database_password="unreachable-test-password",
        database_connect_timeout_seconds=1,
        _env_file=None,
    )

    with TestClient(create_app(settings=settings)) as client:
        readiness = client.get("/ready")
        health = client.get("/health")
        secret_content = "DOCUMENT_SECRET_NOT_FOR_LOGS"
        with caplog.at_level(logging.INFO, logger="researchflow.request"):
            upload = client.post(
                "/api/v1/documents",
                data={"document_key": "db-down", "title": "Unavailable"},
                files={"file": ("safe.txt", secret_content.encode(), "text/plain")},
            )

    assert readiness.status_code == 503
    assert readiness.json() == {
        "status": "not_ready",
        "database": "unavailable",
        "pgvector_version": None,
    }
    assert health.status_code == 200
    assert upload.status_code == 503
    assert upload.json()["error"]["code"] == "database_unavailable"
    assert secret_content not in "\n".join(record.getMessage() for record in caplog.records)

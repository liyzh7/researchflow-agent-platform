"""System endpoint tests."""

from fastapi.testclient import TestClient

from researchflow import __version__


def test_health_returns_stable_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "researchflow"}


def test_info_describes_running_service(client: TestClient) -> None:
    response = client.get("/api/v1/info")

    assert response.status_code == 200
    assert response.json() == {
        "project_name": "ResearchFlow",
        "version": __version__,
        "environment": "test",
        "status": "operational",
        "provider_mode": "mock",
        "rag_enabled": False,
        "agent_enabled": False,
        "upload_max_bytes": 2 * 1024 * 1024,
    }


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

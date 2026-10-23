"""Provider failure mapping tests."""

from researchflow.core.config import Settings
from researchflow.main import create_app
from researchflow.providers.errors import (
    LLMProviderBusyError,
    LLMProviderRedirectError,
    LLMProviderTimeoutError,
    LLMProviderUnavailableError,
)
from researchflow.providers.llm import LLMRequest, LLMResponse
from tests.auth_support import UnitTestClient as TestClient


class FailingProvider:
    def __init__(self, error: Exception) -> None:
        self.name = "failing-test-provider"
        self._error = error

    async def generate(self, _request: LLMRequest, *, timeout_seconds: float) -> LLMResponse:
        assert 0 < timeout_seconds <= 5.0  # Queue wait is deducted from the provider deadline.
        raise self._error


def test_provider_timeout_returns_gateway_timeout() -> None:
    app = create_app(
        settings=Settings(
            environment="test", database_password="unit-test-password", _env_file=None
        ),
        llm_provider=FailingProvider(LLMProviderTimeoutError()),
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"question": "invented tray timeout request"})

    assert response.status_code == 504
    assert response.json()["error"] == {
        "code": "provider_timeout",
        "message": "The model provider timed out.",
        "details": [],
    }


def test_provider_error_returns_service_unavailable() -> None:
    app = create_app(
        settings=Settings(
            environment="test", database_password="unit-test-password", _env_file=None
        ),
        llm_provider=FailingProvider(LLMProviderUnavailableError()),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat", json={"question": "invented tray unavailable request"}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"


def test_model_queue_busy_is_not_misreported_as_model_offline() -> None:
    app = create_app(
        settings=Settings(
            environment="test", database_password="unit-test-password", _env_file=None
        ),
        llm_provider=FailingProvider(LLMProviderBusyError()),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"question": "invented tray queue request"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_busy"


def test_redirect_policy_has_a_safe_explicit_api_error() -> None:
    app = create_app(
        settings=Settings(
            environment="test", database_password="unit-test-password", _env_file=None
        ),
        llm_provider=FailingProvider(LLMProviderRedirectError()),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"question": "invented tray redirect request"})
    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "provider_redirect_blocked",
        "message": "Provider redirects are disabled. Configure the final endpoint URL.",
        "details": [],
    }

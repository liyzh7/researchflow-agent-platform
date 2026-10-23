"""Mock chat success and validation tests."""

import logging
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient


def test_mock_chat_returns_deterministic_disclosed_response(client: TestClient) -> None:
    payload = {"question": "Count the empty trays in this invented sorting exercise."}

    first = client.post("/api/v1/chat", json=payload)
    second = client.post("/api/v1/chat", json=payload)

    assert first.status_code == 200
    assert first.json() == second.json()
    digest = sha256(payload["question"].encode()).hexdigest()[:12]
    expected_answer = " ".join(
        (
            f"[MOCK] Deterministic test response ({digest}).",
            "No model inference was performed.",
        )
    )
    assert first.json() == {
        "answer": expected_answer,
        "provider_mode": "mock",
        "provider_model": "deterministic-mock-v1",
        "is_mock": True,
        "rag_enabled": False,
        "agent_enabled": False,
        "citations": [],
    }


@pytest.mark.parametrize("question", ["", "   "])
def test_empty_question_is_rejected(client: TestClient, question: str) -> None:
    response = client.post("/api/v1/chat", json={"question": question})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_overlong_question_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"question": "x" * 2001})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_invalid_json_uses_validation_error_envelope(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_request_log_does_not_include_question_or_sensitive_header(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    secret_question = "TEST_SECRET_QUESTION_82f61"
    secret_header = "TEST_SECRET_TOKEN_5a17c"

    with caplog.at_level(logging.INFO, logger="researchflow.request"):
        response = client.post(
            "/api/v1/chat?token=TEST_SECRET_QUERY_91dce",
            json={"question": secret_question},
            headers={"authorization": f"Bearer {secret_header}"},
        )

    assert response.status_code == 200
    combined_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_question not in combined_logs
    assert secret_header not in combined_logs
    assert "TEST_SECRET_QUERY_91dce" not in combined_logs
    assert "path=/api/v1/chat" in combined_logs

"""Retrieval endpoint validation and stable response tests."""

from collections.abc import Iterator
from uuid import uuid4

import pytest

from researchflow.main import create_app
from researchflow.retrieval.types import RetrievalHit, RetrievalResult
from tests.auth_support import UnitTestClient as TestClient
from tests.support import DeterministicEmbeddingProvider


class StubRetrievalService:
    def __init__(self) -> None:
        self.provider = DeterministicEmbeddingProvider()

    async def search(self, **arguments) -> RetrievalResult:  # type: ignore[no-untyped-def]
        identifier = uuid4()
        hit = RetrievalHit(
            document_id=identifier,
            document_key=arguments.get("document_key") or "paper-1",
            title="Paper",
            version_id=uuid4(),
            version_number=2,
            chunk_id=uuid4(),
            chunk_index=0,
            segment_id=uuid4(),
            segment_index=0,
            content="toy shelf slots",
            char_start=10,
            char_end=25,
            line_start=2,
            line_end=2,
            retrieval_method=arguments["mode"],
        )
        return RetrievalResult(
            status="ok",
            mode=arguments["mode"],
            items=(hit,),
            model=self.provider.info,
            rrf_k=60 if arguments["mode"] == "hybrid" else None,
        )


@pytest.fixture
def retrieval_client(settings) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    application = create_app(settings=settings)
    application.state.retrieval_service = StubRetrievalService()
    with TestClient(application) as client:
        yield client


def test_search_returns_only_stored_evidence(retrieval_client: TestClient) -> None:
    response = retrieval_client.post(
        "/api/v1/retrieval/search",
        json={"query": "evidence", "mode": "hybrid", "top_k": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["items"][0]["content"] == "toy shelf slots"
    assert body["items"][0]["char_start"] == 10
    assert body["rrf_k"] == 60
    assert "answer" not in body


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": " "},
        {"query": "x" * 1001},
        {"query": "ok", "top_k": 0},
        {"query": "ok", "mode": "unknown"},
        {"query": "ok", "document_key": "a", "document_id": str(uuid4())},
        {"query": "ok", "unexpected": True},
    ],
)
def test_search_rejects_invalid_requests(
    retrieval_client: TestClient, payload: dict[str, object]
) -> None:
    response = retrieval_client.post("/api/v1/retrieval/search", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

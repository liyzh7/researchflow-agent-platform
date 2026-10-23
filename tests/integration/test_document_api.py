"""Real PostgreSQL integration tests for migrations and document workflows."""

import asyncio
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from researchflow.core.config import Settings
from researchflow.db.database import Database
from researchflow.documents.chunker import CharacterChunker
from researchflow.documents.parsers import ParserRegistry
from researchflow.documents.service import DocumentService
from researchflow.documents.types import TextChunk
from researchflow.indexing.errors import IndexingError
from researchflow.indexing.service import IndexingService
from researchflow.main import create_app
from researchflow.providers.errors import (
    EmbeddingProviderInvalidVectorError,
    EmbeddingProviderTimeoutError,
)
from researchflow.providers.local_embedding import LocalEmbeddingProvider
from researchflow.rag.citations import CitationService, PostgresEvidenceSourceRepository
from researchflow.rag.errors import CitationValidationError
from researchflow.rag.types import GeneratedCitation, PromptEvidence
from researchflow.reranking.cross_encoder import LocalCrossEncoderReranker
from researchflow.retrieval.service import RetrievalService
from tests.auth_support import AuthenticatedTestClient as TestClient
from tests.database_support import database_settings
from tests.docker_support import restart_test_database
from tests.process_support import python_module
from tests.support import (
    DeterministicEmbeddingProvider,
    DeterministicReranker,
    InvalidVectorEmbeddingProvider,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    if os.environ.get("RESEARCHFLOW_RUN_INTEGRATION") != "1":
        pytest.skip("set RESEARCHFLOW_RUN_INTEGRATION=1 to run PostgreSQL integration tests")
    local = database_settings("researchflow_test")
    return local.model_copy(
        update={
            "environment": "test",
            "database_name": "researchflow_test",
            "auth_login_per_minute": 120,
        }
    )


def run_alembic(settings: Settings, *arguments: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "RESEARCHFLOW_ENVIRONMENT": "test",
            "RESEARCHFLOW_DATABASE_HOST": settings.database_host,
            "RESEARCHFLOW_DATABASE_PORT": str(settings.database_port),
            "RESEARCHFLOW_DATABASE_USER": settings.database_user,
            "RESEARCHFLOW_DATABASE_PASSWORD": settings.database_password.get_secret_value(),
            "RESEARCHFLOW_DATABASE_NAME": settings.database_name,
        }
    )
    result = subprocess.run(
        python_module("alembic", *arguments),
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="session", autouse=True)
def migrated_database(integration_settings: Settings) -> None:
    # Dedicated test schema is upgraded non-destructively. Development/volume are never reset.
    # Stage 6 retains existing dedicated test data; owned rows forbid a blind identity downgrade.
    run_alembic(integration_settings, "upgrade", "head")


@pytest.fixture
def client(integration_settings: Settings):  # type: ignore[no-untyped-def]
    with TestClient(create_app(settings=integration_settings)) as test_client:
        yield test_client


def document_key(label: str) -> str:
    return f"itest-{label}-{uuid4().hex}"


def upload(
    client: TestClient,
    key: str,
    content: str,
    *,
    filename: str = "paper.md",
    title: str = "Invented card-sort notes",
):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/documents",
        data={"document_key": key, "title": title},
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
    )


def connect(settings: Settings):  # type: ignore[no-untyped-def]
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password.get_secret_value(),
        dbname=settings.database_name,
    )


def test_migration_upgrade_is_idempotent(integration_settings: Settings) -> None:
    run_alembic(integration_settings, "upgrade", "head")
    run_alembic(integration_settings, "upgrade", "head")


def test_readiness_reports_postgres_and_pgvector(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["database"] == "available"
    assert response.json()["pgvector_version"] == "0.8.7"


def test_upload_query_and_exact_persistence(
    client: TestClient, integration_settings: Settings
) -> None:
    key = document_key("query")
    raw_text = "# Cards\r\n\r\nTray B has a blue card; 格子数为三。"
    created = upload(client, key, raw_text)

    assert created.status_code == 201
    payload = created.json()
    assert payload["version_number"] == 1
    assert payload["is_duplicate"] is False
    assert payload["chunk_count"] == 1

    listed = client.get("/api/v1/documents", params={"offset": 0, "limit": 100})
    detail = client.get(f"/api/v1/documents/{payload['document_id']}")
    chunks = client.get(
        f"/api/v1/documents/{payload['document_id']}/versions/{payload['version_id']}/chunks"
    )

    assert listed.status_code == 200
    assert any(item["document_key"] == key for item in listed.json()["items"])
    assert detail.status_code == 200
    assert detail.json()["current_version"]["sha256"] == payload["sha256"]
    assert chunks.status_code == 200
    assert chunks.json()[0]["content"] == raw_text.replace("\r\n", "\n")
    assert chunks.json()[0]["char_start"] == 0
    assert chunks.json()[0]["char_end"] == len(raw_text.replace("\r\n", "\n"))

    with connect(integration_settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT dv.raw_content, dv.parsed_text
            FROM document_versions AS dv
            JOIN documents AS d ON d.id = dv.document_id
            WHERE d.document_key = %s
            """,
            (key,),
        )
        raw_content, parsed_text = cursor.fetchone()
    assert raw_content == raw_text.encode("utf-8")
    assert parsed_text == raw_text.replace("\r\n", "\n")


def test_duplicate_latest_upload_is_idempotent(client: TestClient) -> None:
    key = document_key("duplicate")
    first = upload(client, key, "one blue card beside three wooden tokens")
    duplicate = upload(
        client, key, "one blue card beside three wooden tokens", title="Renamed card-sort notes"
    )
    versions = client.get(f"/api/v1/documents/{first.json()['document_id']}/versions")

    assert first.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.json()["is_duplicate"] is True
    assert duplicate.json()["version_id"] == first.json()["version_id"]
    assert [version["version_number"] for version in versions.json()] == [1]


def test_changed_content_and_a_b_a_create_monotonic_history(client: TestClient) -> None:
    key = document_key("history")
    first = upload(client, key, "tray B receives the blue card")
    second = upload(client, key, "tray D receives the yellow card")
    third = upload(client, key, "tray B receives the blue card")
    versions = client.get(f"/api/v1/documents/{first.json()['document_id']}/versions")

    assert [first.status_code, second.status_code, third.status_code] == [201, 201, 201]
    assert [
        first.json()["version_number"],
        second.json()["version_number"],
        third.json()["version_number"],
    ] == [1, 2, 3]
    assert [version["version_number"] for version in versions.json()] == [3, 2, 1]


def test_concurrent_same_key_upload_has_one_version(integration_settings: Settings) -> None:
    key = document_key("concurrent")

    def perform_upload(_: int) -> tuple[int, dict]:
        with TestClient(create_app(settings=integration_settings)) as concurrent_client:
            response = upload(
                concurrent_client, key, "two simultaneous writes of the same invented tray note"
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(perform_upload, range(6)))

    with TestClient(create_app(settings=integration_settings)) as query_client:
        document_id = results[0][1]["document_id"]
        versions = query_client.get(f"/api/v1/documents/{document_id}/versions")

    assert sum(status == 201 for status, _ in results) == 1
    assert sum(status == 200 for status, _ in results) == 5
    assert {body["version_id"] for _, body in results} == {results[0][1]["version_id"]}
    assert [version["version_number"] for version in versions.json()] == [1]


class DuplicateIndexChunker(CharacterChunker):
    def split(self, text: str) -> list[TextChunk]:
        base = super().split(text)[0]
        duplicate = TextChunk(
            index=base.index,
            content=base.content,
            char_start=base.char_start,
            char_end=base.char_end,
            line_start=base.line_start,
            line_end=base.line_end,
            chunker_version=base.chunker_version,
        )
        return [base, duplicate]


def test_failed_chunk_insert_rolls_back_version_and_records_failure(
    integration_settings: Settings,
) -> None:
    key = document_key("rollback")
    with TestClient(create_app(settings=integration_settings)) as baseline_client:
        first = upload(baseline_client, key, "a blue card remains in tray B")

    database = Database(integration_settings)
    app = create_app(settings=integration_settings, database=database)
    app.state.document_service = DocumentService(
        database,
        parser_registry=ParserRegistry(),
        chunker=DuplicateIndexChunker(chunk_size=800, overlap=100),
    )
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        failed = upload(
            failing_client, key, "a yellow card must never survive the failed transaction"
        )

    with TestClient(create_app(settings=integration_settings)) as query_client:
        versions = query_client.get(f"/api/v1/documents/{first.json()['document_id']}/versions")

    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "document_ingestion_failed"
    assert [version["version_number"] for version in versions.json()] == [1]
    with connect(integration_settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE document_key = %s AND status = 'failed'",
            (key,),
        )
        assert cursor.fetchone()[0] == 1


def test_missing_document_returns_public_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/documents/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


@pytest.mark.local_docker
def test_container_restart_preserves_document(
    client: TestClient, integration_settings: Settings
) -> None:
    if os.environ.get("RESEARCHFLOW_RUN_RESTART_TEST") != "1":
        pytest.skip("set RESEARCHFLOW_RUN_RESTART_TEST=1 for the explicit persistence test")
    key = document_key("restart")
    created = upload(client, key, "the invented tray label persists across a restart")

    restart_test_database(integration_settings)

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        readiness = client.get("/ready")
        if readiness.status_code == 200:
            break
        time.sleep(1)
    else:
        pytest.fail("PostgreSQL did not become ready after restart")

    detail = client.get(f"/api/v1/documents/{created.json()['document_id']}")
    assert detail.status_code == 200
    assert detail.json()["document_key"] == key
    assert integration_settings.database_name == "researchflow_test"


def test_stage3_migration_installs_vector_schema_and_indexes(
    integration_settings: Settings,
) -> None:
    with connect(integration_settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')"
        )
        extensions = dict(cursor.fetchall())
        cursor.execute(
            """
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_attribute AS attribute
            JOIN pg_class AS relation ON relation.oid = attribute.attrelid
            WHERE relation.relname = 'index_segments'
              AND attribute.attname = 'embedding'
            """
        )
        embedding_type = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'index_segments'
            """
        )
        indexes = dict(cursor.fetchall())

    assert extensions == {"vector": "0.8.7", "pg_trgm": extensions["pg_trgm"]}
    assert embedding_type == "vector(384)"
    assert "USING hnsw" in indexes["ix_index_segments_embedding_hnsw"]
    assert "vector_cosine_ops" in indexes["ix_index_segments_embedding_hnsw"]
    assert "USING gin" in indexes["ix_index_segments_search_vector_gin"]
    assert "gin_trgm_ops" in indexes["ix_index_segments_content_trgm_gin"]


def test_indexing_is_idempotent_and_persists_exact_source_offsets(
    client: TestClient, integration_settings: Settings
) -> None:
    key = document_key("index")
    source = (
        "# Abstract\n\nHybrid retrieval combines dense vectors and keywords.\n"
        "中文证据保持原始位置。"
    )
    created = upload(client, key, source).json()
    provider = DeterministicEmbeddingProvider()
    database = Database(integration_settings)

    async def exercise():  # type: ignore[no-untyped-def]
        service = IndexingService(database, provider, integration_settings)
        first = await service.index_version(created["version_id"])
        second = await service.index_version(created["version_id"])
        await database.dispose()
        return first, second

    first, second = asyncio.run(exercise())
    assert first.status == "completed"
    assert second.status == "already_completed"
    assert second.segment_count == first.segment_count

    with connect(integration_settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT segment.content, segment.char_start, segment.char_end,
                   segment.token_count, vector_dims(segment.embedding)
            FROM index_segments AS segment
            WHERE segment.document_version_id = %s
            ORDER BY segment.segment_index
            """,
            (created["version_id"],),
        )
        rows = cursor.fetchall()
    assert rows
    for content, char_start, char_end, token_count, dimensions in rows:
        assert source[char_start:char_end] == content
        assert token_count <= provider.info.max_input_tokens
        assert dimensions == 384


def test_indexing_failure_is_recorded_and_retryable(
    client: TestClient, integration_settings: Settings
) -> None:
    created = upload(
        client, document_key("retry"), "a toy drawer note that can be indexed after retry"
    ).json()
    provider = DeterministicEmbeddingProvider(fail_count=1)
    database = Database(integration_settings)

    async def exercise():  # type: ignore[no-untyped-def]
        service = IndexingService(database, provider, integration_settings)
        with pytest.raises(EmbeddingProviderTimeoutError):
            await service.index_version(created["version_id"])
        retried = await service.index_version(created["version_id"])
        await database.dispose()
        return retried

    retried = asyncio.run(exercise())
    assert retried.status == "completed"
    assert retried.attempt_count == 2


def test_invalid_vectors_fail_before_database_storage(
    client: TestClient, integration_settings: Settings
) -> None:
    created = upload(
        client, document_key("invalid-vector"), "a toy drawer note paired with an invalid vector"
    ).json()
    database = Database(integration_settings)

    async def exercise():  # type: ignore[no-untyped-def]
        service = IndexingService(database, InvalidVectorEmbeddingProvider(), integration_settings)
        with pytest.raises(EmbeddingProviderInvalidVectorError):
            await service.index_version(created["version_id"])
        await database.dispose()

    asyncio.run(exercise())
    with connect(integration_settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM index_segments WHERE document_version_id = %s",
            (created["version_id"],),
        )
        assert cursor.fetchone()[0] == 0


def test_wrong_embedding_dimension_is_rejected_before_persistence(
    integration_settings: Settings,
) -> None:
    provider = DeterministicEmbeddingProvider()
    provider.info = provider.info.model_copy(update={"dimension": 385})
    database = Database(integration_settings)

    async def exercise():  # type: ignore[no-untyped-def]
        service = IndexingService(database, provider, integration_settings)
        with pytest.raises(IndexingError, match="dimension"):
            await service.ensure_model_config()
        await database.dispose()

    asyncio.run(exercise())


def test_concurrent_indexing_has_one_complete_index(
    client: TestClient, integration_settings: Settings
) -> None:
    created = upload(
        client, document_key("index-concurrent"), "a toy drawer note indexed by competing workers"
    ).json()
    database = Database(integration_settings)
    provider = DeterministicEmbeddingProvider()

    async def exercise():  # type: ignore[no-untyped-def]
        service = IndexingService(database, provider, integration_settings)
        results = await asyncio.gather(
            service.index_version(created["version_id"]),
            service.index_version(created["version_id"]),
        )
        final = await service.index_version(created["version_id"])
        await database.dispose()
        return results, final

    results, final = asyncio.run(exercise())
    assert any(result.status == "completed" for result in results)
    assert all(
        result.status in {"completed", "already_completed", "in_progress"} for result in results
    )
    assert final.status == "already_completed"
    with connect(integration_settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM index_segments WHERE document_version_id = %s",
            (created["version_id"],),
        )
        assert cursor.fetchone()[0] == final.segment_count


def test_retrieval_modes_filters_current_version_and_persists_across_engines(
    client: TestClient, integration_settings: Settings
) -> None:
    key = document_key("retrieval")
    first = upload(
        client, key, "The retired magenta square is present in the first ledger only."
    ).json()
    provider = DeterministicEmbeddingProvider()
    database = Database(integration_settings)

    async def index_first():  # type: ignore[no-untyped-def]
        await IndexingService(database, provider, integration_settings).index_version(
            first["version_id"]
        )
        await database.dispose()

    asyncio.run(index_first())
    second = upload(
        client, key, "The latest blue-card ledger lists three tokens. 最新格子帐册。"
    ).json()
    database = Database(integration_settings)

    async def index_second():  # type: ignore[no-untyped-def]
        await IndexingService(database, provider, integration_settings).index_version(
            second["version_id"]
        )
        await database.dispose()

    asyncio.run(index_second())

    # A fresh engine proves retrieval reads the persisted database state.
    fresh_database = Database(integration_settings)
    app = create_app(
        settings=integration_settings,
        embedding_provider=provider,
        database=fresh_database,
    )
    with TestClient(app) as retrieval_client:
        old = retrieval_client.post(
            "/api/v1/retrieval/search",
            json={"query": "magenta", "mode": "keyword", "document_key": key},
        )
        chinese = retrieval_client.post(
            "/api/v1/retrieval/search",
            json={"query": "最新格子帐册", "mode": "keyword", "document_key": key},
        )
        for mode in ("vector", "keyword", "hybrid"):
            response = retrieval_client.post(
                "/api/v1/retrieval/search",
                json={"query": "latest blue-card ledger", "mode": mode, "top_k": 5},
            )
            assert response.status_code == 200
            assert response.json()["items"]
            assert len(response.json()["items"]) <= 5
            assert all(
                item["version_id"] == second["version_id"]
                for item in response.json()["items"]
                if item["document_key"] == key
            )

        missing_filter = retrieval_client.post(
            "/api/v1/retrieval/search",
            json={"query": "blue-card", "mode": "hybrid", "document_key": "missing-key"},
        )

    assert old.status_code == 200
    assert old.json()["items"] == []
    assert chinese.json()["items"][0]["version_id"] == second["version_id"]
    assert missing_filter.json()["status"] == "no_indexed_documents"


def test_unindexed_current_version_does_not_fall_back_to_indexed_history(
    client: TestClient, integration_settings: Settings
) -> None:
    key = document_key("unindexed-current")
    first = upload(client, key, "retired tray ledger already indexed").json()
    provider = DeterministicEmbeddingProvider()
    database = Database(integration_settings)

    async def index_history():  # type: ignore[no-untyped-def]
        await IndexingService(database, provider, integration_settings).index_version(
            first["version_id"]
        )
        await database.dispose()

    asyncio.run(index_history())
    current = upload(client, key, "a replacement tray ledger waiting to be indexed").json()
    assert current["version_number"] == 2

    app = create_app(settings=integration_settings, embedding_provider=provider)
    with TestClient(app) as retrieval_client:
        response = retrieval_client.post(
            "/api/v1/retrieval/search",
            json={"query": "retired tray ledger", "mode": "hybrid", "document_key": key},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "no_indexed_documents"
    assert response.json()["items"] == []


def test_exact_and_hnsw_vector_paths_return_the_same_top_hit(
    client: TestClient, integration_settings: Settings
) -> None:
    key = document_key("vector-plan")
    created = upload(client, key, "an invented tray ledger with six circular tokens").json()
    provider = DeterministicEmbeddingProvider()
    database = Database(integration_settings)

    async def exercise():  # type: ignore[no-untyped-def]
        await IndexingService(database, provider, integration_settings).index_version(
            created["version_id"]
        )
        retrieval = RetrievalService(database, provider, integration_settings)
        approximate = await retrieval.search(
            query="an invented tray ledger with six circular tokens",
            top_k=1,
            mode="vector",
            document_key=key,
        )
        exact = await retrieval.vector_search_exact(
            query="an invented tray ledger with six circular tokens", top_k=1, document_key=key
        )
        approximate_plan = await retrieval.explain_vector_query(
            query="an invented tray ledger with six circular tokens", top_k=5, approximate=True
        )
        exact_plan = await retrieval.explain_vector_query(
            query="an invented tray ledger with six circular tokens", top_k=5, approximate=False
        )
        await database.dispose()
        return approximate, exact, approximate_plan, exact_plan

    approximate, exact, approximate_plan, exact_plan = asyncio.run(exercise())
    assert approximate.items[0].segment_id == exact[0].segment_id
    assert any("index_segments" in line for line in approximate_plan)
    assert any("index_segments" in line for line in exact_plan)


def test_retrieval_logs_query_length_not_query_text(
    client: TestClient,
    integration_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    key = document_key("safe-log")
    created = upload(client, key, "a toy ledger used to inspect request logging").json()
    provider = DeterministicEmbeddingProvider()
    database = Database(integration_settings)

    async def exercise():  # type: ignore[no-untyped-def]
        await IndexingService(database, provider, integration_settings).index_version(
            created["version_id"]
        )
        retrieval = RetrievalService(database, provider, integration_settings)
        await retrieval.search(
            query="SENSITIVE-QUERY-VALUE-9341", top_k=1, mode="hybrid", document_key=key
        )
        await database.dispose()

    asyncio.run(exercise())
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "query_chars=26" in messages
    assert "SENSITIVE-QUERY-VALUE-9341" not in messages


def test_citation_refetch_validates_real_postgres_source(
    client: TestClient, integration_settings: Settings
) -> None:
    key = document_key("citation")
    created = upload(client, key, "The toy shelf ledger records two empty slots.").json()
    provider = DeterministicEmbeddingProvider()
    database = Database(integration_settings)

    async def exercise():  # type: ignore[no-untyped-def]
        await IndexingService(database, provider, integration_settings).index_version(
            created["version_id"]
        )
        retrieved = await RetrievalService(database, provider, integration_settings).search(
            query="toy shelf ledger empty slots",
            top_k=1,
            mode="hybrid",
            document_key=key,
        )
        hit = retrieved.items[0]
        verified = await CitationService(PostgresEvidenceSourceRepository(database)).verify(
            (GeneratedCitation(evidence_id="E1"),),
            (PromptEvidence(evidence_id="E1", hit=hit),),
        )
        await database.dispose()
        return hit, verified[0]

    hit, citation = asyncio.run(exercise())
    assert citation.segment_id == hit.segment_id
    assert citation.excerpt == "The toy shelf ledger records two empty slots."


def test_citation_repository_rejects_old_version_after_update(
    client: TestClient, integration_settings: Settings
) -> None:
    key = document_key("citation-stale")
    first = upload(client, key, "The retired toy shelf ledger records one slot.").json()
    provider = DeterministicEmbeddingProvider()
    database = Database(integration_settings)

    async def retrieve_old():  # type: ignore[no-untyped-def]
        await IndexingService(database, provider, integration_settings).index_version(
            first["version_id"]
        )
        result = await RetrievalService(database, provider, integration_settings).search(
            query="retired toy shelf ledger",
            top_k=1,
            mode="hybrid",
            document_key=key,
        )
        return result.items[0]

    old_hit = asyncio.run(retrieve_old())
    assert (
        upload(client, key, "The replacement toy shelf ledger records four slots.").status_code
        == 201
    )

    async def verify_old():  # type: ignore[no-untyped-def]
        with pytest.raises(CitationValidationError):
            await CitationService(PostgresEvidenceSourceRepository(database)).verify(
                (GeneratedCitation(evidence_id="E1"),),
                (PromptEvidence(evidence_id="E1", hit=old_hit),),
            )
        await database.dispose()

    asyncio.run(verify_old())


def test_rag_api_uses_real_postgres_retrieval_and_citation_refetch(
    integration_settings: Settings,
) -> None:
    rag_settings = integration_settings.model_copy(
        update={
            "rag_enabled": True,
            "reranker_enabled": True,
            "evidence_min_rerank_score": -2.0,
            "evidence_min_query_coverage": 0.2,
        }
    )
    provider = DeterministicEmbeddingProvider()
    app = create_app(
        settings=rag_settings,
        embedding_provider=provider,
        reranker_provider=DeterministicReranker(),
    )
    key = document_key("rag-e2e")

    with TestClient(app) as rag_client:
        created = upload(
            rag_client,
            key,
            "The invented tray ledger assigns a count of 7 to the blue circles.",
        ).json()
        database = Database(rag_settings)

        async def index_document():  # type: ignore[no-untyped-def]
            result = await IndexingService(database, provider, rag_settings).index_version(
                created["version_id"]
            )
            await database.dispose()
            return result

        assert asyncio.run(index_document()).status == "completed"
        response = rag_client.post(
            "/api/v1/rag/answer",
            json={
                "query": "Which count does the invented tray ledger assign to blue circles?",
                "document_key": key,
                "top_k": 1,
                "rerank_enabled": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["evidence_status"] == "ANSWERABLE"
    assert payload["is_mock"] is True
    assert payload["answer"].startswith("[MOCK]")
    assert payload["reranker_model"] == "test-reranker"
    assert len(payload["citations"]) == 1
    citation = payload["citations"][0]
    assert citation["document_key"] == key
    assert citation["version_id"] == created["version_id"]
    assert citation["excerpt"] == (
        "The invented tray ledger assigns a count of 7 to the blue circles."
    )


@pytest.mark.real_embedding
@pytest.mark.real_reranker
@pytest.mark.skipif(
    os.environ.get("RESEARCHFLOW_RUN_REAL_EMBEDDING") != "1"
    or os.environ.get("RESEARCHFLOW_RUN_REAL_RERANKER") != "1",
    reason="set both real-model opt-in flags after model download approval",
)
def test_rag_api_with_real_e5_bge_and_postgres(
    integration_settings: Settings,
) -> None:
    rag_settings = integration_settings.model_copy(
        update={
            "rag_enabled": True,
            "reranker_enabled": True,
            "evidence_min_rerank_score": -100.0,  # Smoke-only permissive gate, not calibration.
            "evidence_min_query_coverage": 0.2,
        }
    )
    key = document_key("real-rag-e2e")
    with TestClient(
        create_app(
            settings=rag_settings,
            embedding_provider=DeterministicEmbeddingProvider(),
            reranker_provider=DeterministicReranker(),
        )
    ) as upload_client:
        created = upload(
            upload_client,
            key,
            "The invented tray ledger lists seven blue circles and two empty compartments.",
        ).json()

    indexing_database = Database(rag_settings)
    indexing_embedding = LocalEmbeddingProvider(rag_settings)

    async def index_real_document():  # type: ignore[no-untyped-def]
        result = await IndexingService(
            indexing_database,
            indexing_embedding,
            rag_settings,
        ).index_version(created["version_id"])
        await indexing_database.dispose()
        return result

    assert asyncio.run(index_real_document()).status == "completed"

    app = create_app(
        settings=rag_settings,
        embedding_provider=LocalEmbeddingProvider(rag_settings),
        reranker_provider=LocalCrossEncoderReranker(rag_settings),
    )
    with TestClient(app) as rag_client:
        response = rag_client.post(
            "/api/v1/rag/answer",
            json={
                "query": (
                    "How many blue circles and empty compartments "
                    "are listed in the invented tray ledger?"
                ),
                "document_key": key,
                "top_k": 1,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["is_mock"] is True
    assert payload["evidence_status"] == "ANSWERABLE"
    assert payload["reranker_model"] == "BAAI/bge-reranker-base"
    assert payload["reranker_revision"] == ("2cfc18c9415c912f9d8155881c133215df768a70")
    assert payload["citations"][0]["version_id"] == created["version_id"]
    assert payload["citations"][0]["excerpt"].endswith("two empty compartments.")


@pytest.mark.local_docker
def test_index_persists_after_container_restart(integration_settings: Settings) -> None:
    if os.environ.get("RESEARCHFLOW_RUN_RESTART_TEST") != "1":
        pytest.skip("set RESEARCHFLOW_RUN_RESTART_TEST=1 for index restart persistence")
    key = document_key("index-restart")
    provider = DeterministicEmbeddingProvider()
    with TestClient(
        create_app(settings=integration_settings, embedding_provider=provider)
    ) as upload_client:
        created = upload(upload_client, key, "a toy shelf ledger that survives a restart").json()
    database = Database(integration_settings)

    async def index_before_restart():  # type: ignore[no-untyped-def]
        result = await IndexingService(database, provider, integration_settings).index_version(
            created["version_id"]
        )
        await database.dispose()
        return result

    assert asyncio.run(index_before_restart()).status == "completed"

    restart_test_database(integration_settings)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with connect(integration_settings) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone()[0] == 1:
                    break
        except psycopg.OperationalError:
            time.sleep(1)
    else:
        pytest.fail("PostgreSQL did not become ready after index restart")

    with TestClient(
        create_app(settings=integration_settings, embedding_provider=provider)
    ) as retrieval_client:
        response = retrieval_client.post(
            "/api/v1/retrieval/search",
            json={
                "query": "a toy shelf ledger that survives a restart",
                "mode": "vector",
                "document_key": key,
            },
        )
    assert response.status_code == 200
    assert response.json()["items"][0]["version_id"] == created["version_id"]

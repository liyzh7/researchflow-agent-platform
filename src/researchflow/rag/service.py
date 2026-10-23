"""Evidence-gated RAG use case with structured output and citation revalidation."""

import time
from uuid import UUID

from pydantic import ValidationError

from researchflow.auth.audit import emit_audit
from researchflow.core.config import Settings
from researchflow.observability.tracing import operation
from researchflow.providers.errors import LLMProviderInvalidResponseError
from researchflow.providers.llm import LLMProvider, LLMRequest
from researchflow.rag.citations import CitationService
from researchflow.rag.errors import RAGConfigurationError
from researchflow.rag.evidence import EvidenceGate
from researchflow.rag.prompt import RAGPromptBuilder
from researchflow.rag.types import (
    EvidenceGateResult,
    EvidenceReason,
    EvidenceStatus,
    GroundedAnswerPayload,
    RAGAnswerResult,
    RAGTiming,
)
from researchflow.reranking.service import RerankService
from researchflow.retrieval.service import RetrievalService
from researchflow.retrieval.types import SearchMode

_REFUSAL = "The indexed evidence is insufficient to answer this question."


class RAGService:
    def __init__(
        self,
        *,
        settings: Settings,
        retrieval: RetrievalService,
        reranker: RerankService | None,
        gate: EvidenceGate,
        prompt_builder: RAGPromptBuilder,
        provider: LLMProvider,
        citations: CitationService,
    ) -> None:
        self._settings = settings
        self._retrieval = retrieval
        self._reranker = reranker
        self._gate = gate
        self._prompt_builder = prompt_builder
        self._provider = provider
        self._citations = citations

    async def answer(
        self,
        **parameters,
    ) -> RAGAnswerResult:
        database = getattr(self._retrieval, "database", None)
        try:
            result = await self._answer(**parameters)
        except Exception:
            if database is not None:
                await emit_audit(database, "rag.result", "rag", outcome="failed", status="failed")
            raise
        if database is not None:
            await emit_audit(database, "rag.result", "rag", status=result.status)
        return result

    async def _answer(
        self,
        *,
        query: str,
        retrieval_mode: SearchMode,
        top_k: int,
        rerank_enabled: bool,
        document_id: UUID | None = None,
        document_key: str | None = None,
    ) -> RAGAnswerResult:
        if not self._settings.rag_enabled:
            raise RAGConfigurationError
        started = time.perf_counter()
        candidate_k = min(
            self._settings.reranker_candidate_limit,
            max(top_k, top_k * 2),
        )
        retrieval_started = time.perf_counter()
        retrieval = await self._retrieval.search(
            query=query,
            top_k=candidate_k,
            mode=retrieval_mode,
            document_id=document_id,
            document_key=document_key,
        )
        retrieval_ms = _elapsed_ms(retrieval_started)

        rerank_ms = 0.0
        reranker_model: str | None = None
        reranker_revision: str | None = None
        candidates = retrieval.items
        if rerank_enabled and candidates:
            if self._reranker is None:
                raise RAGConfigurationError("reranker is not configured")
            reranked = await self._reranker.rerank(
                query=query,
                candidates=candidates,
                top_k=top_k,
            )
            candidates = reranked.items
            rerank_ms = reranked.duration_ms
            reranker_model = reranked.model.model_name
            reranker_revision = reranked.model.revision

        gate_started = time.perf_counter()
        with operation("evidence_gate.assess"):
            gate = self._gate.assess(query, candidates)
        gate_ms = _elapsed_ms(gate_started)
        if gate.status is not EvidenceStatus.ANSWERABLE:
            return self._refusal(
                retrieval_mode=retrieval_mode,
                rerank_enabled=rerank_enabled,
                reranker_model=reranker_model,
                reranker_revision=reranker_revision,
                gate=gate,
                timing=RAGTiming(
                    retrieval_ms=retrieval_ms,
                    rerank_ms=rerank_ms,
                    gate_ms=gate_ms,
                    total_ms=_elapsed_ms(started),
                ),
            )

        package = self._prompt_builder.build(query=query, evidence=gate.selected)
        if not package.evidence:
            budget_gate = EvidenceGateResult(
                status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
                reasons=(EvidenceReason.CONTEXT_BUDGET_EXCEEDED,),
                selected=(),
                top_rerank_score=gate.top_rerank_score,
                query_coverage=gate.query_coverage,
            )
            return self._refusal(
                retrieval_mode=retrieval_mode,
                rerank_enabled=rerank_enabled,
                reranker_model=reranker_model,
                reranker_revision=reranker_revision,
                gate=budget_gate,
                timing=RAGTiming(
                    retrieval_ms=retrieval_ms,
                    rerank_ms=rerank_ms,
                    gate_ms=gate_ms,
                    total_ms=_elapsed_ms(started),
                ),
            )

        generation_started = time.perf_counter()
        generated = await self._provider.generate(
            LLMRequest(
                call_kind="rag_answer",
                prompt=package.user_prompt,
                system_prompt=package.system_prompt,
                response_schema_name="researchflow_grounded_answer",
                response_schema=package.response_schema,
            ),
            timeout_seconds=self._settings.llm_timeout_seconds,
        )
        generation_ms = _elapsed_ms(generation_started)
        try:
            payload = GroundedAnswerPayload.model_validate_json(generated.content)
        except (ValidationError, ValueError) as exc:
            raise LLMProviderInvalidResponseError(
                "provider did not return the grounded answer schema"
            ) from exc

        citation_started = time.perf_counter()
        verified = await self._citations.verify(payload.citations, package.evidence)
        citation_ms = _elapsed_ms(citation_started)
        return RAGAnswerResult(
            answer=payload.answer,
            status="answered",
            is_mock=generated.is_mock,
            provider_name=generated.provider,
            provider_model=generated.model,
            citations=verified,
            retrieval_mode=retrieval_mode,
            rerank_enabled=rerank_enabled,
            reranker_model=reranker_model,
            reranker_revision=reranker_revision,
            evidence_status=EvidenceStatus.ANSWERABLE,
            evidence_reasons=(),
            top_rerank_score=gate.top_rerank_score,
            query_coverage=gate.query_coverage,
            timing=RAGTiming(
                retrieval_ms=retrieval_ms,
                rerank_ms=rerank_ms,
                gate_ms=gate_ms,
                generation_ms=generation_ms,
                citation_validation_ms=citation_ms,
                total_ms=_elapsed_ms(started),
            ),
            input_tokens=generated.input_tokens,
            output_tokens=generated.output_tokens,
        )

    @staticmethod
    def _refusal(
        *,
        retrieval_mode: SearchMode,
        rerank_enabled: bool,
        reranker_model: str | None,
        reranker_revision: str | None,
        gate: EvidenceGateResult,
        timing: RAGTiming,
    ) -> RAGAnswerResult:
        return RAGAnswerResult(
            answer=_REFUSAL,
            status="refused",
            is_mock=False,
            provider_name=None,
            provider_model=None,
            citations=(),
            retrieval_mode=retrieval_mode,
            rerank_enabled=rerank_enabled,
            reranker_model=reranker_model,
            reranker_revision=reranker_revision,
            evidence_status=gate.status,
            evidence_reasons=gate.reasons,
            top_rerank_score=gate.top_rerank_score,
            query_coverage=gate.query_coverage,
            timing=timing,
        )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000

"""Citation allow-list, relational source, version, and offset validation."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from researchflow.rag.citations import CitationService
from researchflow.rag.errors import CitationValidationError
from researchflow.rag.types import (
    GeneratedCitation,
    GroundedAnswerPayload,
    PromptEvidence,
)
from tests.support import InMemoryEvidenceSourceRepository, make_retrieval_hit


@pytest.mark.anyio
async def test_valid_citation_is_refetched_and_returned() -> None:
    hit = make_retrieval_hit("The toy drawer contains seven paper squares.")
    service = CitationService(InMemoryEvidenceSourceRepository((hit,)))
    result = await service.verify(
        (GeneratedCitation(evidence_id="E1"),),
        (PromptEvidence(evidence_id="E1", hit=hit),),
    )
    assert result[0].segment_id == hit.segment_id
    assert result[0].excerpt == hit.content


@pytest.mark.anyio
async def test_forged_evidence_id_is_rejected() -> None:
    hit = make_retrieval_hit()
    service = CitationService(InMemoryEvidenceSourceRepository((hit,)))
    with pytest.raises(CitationValidationError, match="unprovided"):
        await service.verify(
            (GeneratedCitation(evidence_id="E999"),),
            (PromptEvidence(evidence_id="E1", hit=hit),),
        )


@pytest.mark.anyio
async def test_wrong_version_is_rejected() -> None:
    hit = make_retrieval_hit()
    repository = InMemoryEvidenceSourceRepository((hit,))
    repository.records[hit.segment_id] = replace(
        repository.records[hit.segment_id], version_number=2
    )
    with pytest.raises(CitationValidationError, match="inconsistent"):
        await CitationService(repository).verify(
            (GeneratedCitation(evidence_id="E1"),),
            (PromptEvidence(evidence_id="E1", hit=hit),),
        )


@pytest.mark.anyio
async def test_wrong_segment_relationship_is_rejected() -> None:
    hit = make_retrieval_hit()
    repository = InMemoryEvidenceSourceRepository((hit,))
    repository.records[hit.segment_id] = replace(
        repository.records[hit.segment_id], chunk_id=make_retrieval_hit().chunk_id
    )
    with pytest.raises(CitationValidationError):
        await CitationService(repository).verify(
            (GeneratedCitation(evidence_id="E1"),),
            (PromptEvidence(evidence_id="E1", hit=hit),),
        )


@pytest.mark.anyio
async def test_mismatched_source_slice_is_rejected() -> None:
    hit = make_retrieval_hit("drawer seven squares")
    repository = InMemoryEvidenceSourceRepository((hit,))
    repository.records[hit.segment_id] = replace(
        repository.records[hit.segment_id], parsed_text="window four circles"
    )
    with pytest.raises(CitationValidationError, match="range"):
        await CitationService(repository).verify(
            (GeneratedCitation(evidence_id="E1"),),
            (PromptEvidence(evidence_id="E1", hit=hit),),
        )


@pytest.mark.anyio
async def test_document_update_makes_old_source_invalid() -> None:
    hit = make_retrieval_hit()
    repository = InMemoryEvidenceSourceRepository((hit,))
    repository.records.clear()
    with pytest.raises(CitationValidationError, match="stale"):
        await CitationService(repository).verify(
            (GeneratedCitation(evidence_id="E1"),),
            (PromptEvidence(evidence_id="E1", hit=hit),),
        )


@pytest.mark.anyio
async def test_duplicate_citations_are_rejected() -> None:
    hit = make_retrieval_hit()
    with pytest.raises(CitationValidationError, match="duplicate"):
        await CitationService(InMemoryEvidenceSourceRepository((hit,))).verify(
            (
                GeneratedCitation(evidence_id="E1"),
                GeneratedCitation(evidence_id="E1"),
            ),
            (PromptEvidence(evidence_id="E1", hit=hit),),
        )


def test_generated_answer_requires_at_least_one_citation() -> None:
    with pytest.raises(ValidationError):
        GroundedAnswerPayload(answer="The toy drawer is full, without a source.", citations=())


@pytest.mark.anyio
async def test_legal_id_does_not_prove_claim_support() -> None:
    hit = make_retrieval_hit("The toy drawer note gives a label but no count.")
    result = await CitationService(InMemoryEvidenceSourceRepository((hit,))).verify(
        (GeneratedCitation(evidence_id="E1"),),
        (PromptEvidence(evidence_id="E1", hit=hit),),
    )
    assert result[0].excerpt == hit.content
    # Claim/evidence support is intentionally a separate evaluation dimension.

"""Reuse the existing citation validator for draft creation, editing and execution."""

from researchflow.agent.errors import AgentError
from researchflow.agent.schemas import DraftContent
from researchflow.rag.citations import CitationService
from researchflow.rag.types import GeneratedCitation, PromptEvidence, VerifiedCitation
from researchflow.retrieval.types import RetrievalHit


async def validate_draft(
    content: DraftContent, evidence: list[dict], citations: CitationService
) -> None:
    by_id = {item["evidence_id"]: VerifiedCitation.model_validate(item) for item in evidence}
    requested: set[str] = set()
    for task in content.tasks:
        source = by_id.get(task.evidence_id)
        if source is None or task.source_quote not in source.excerpt:
            raise AgentError("draft_evidence_mismatch", 422)
        requested.add(task.evidence_id)
    allowed = []
    for source in by_id.values():
        fields = source.model_dump(exclude={"evidence_id", "excerpt"})
        fields["content"] = source.excerpt
        fields["title"] = source.document_key
        fields["retrieval_method"] = "hybrid"
        allowed.append(PromptEvidence(source.evidence_id, RetrievalHit(**fields)))
    await citations.verify(
        tuple(GeneratedCitation(evidence_id=value) for value in sorted(requested)),
        tuple(allowed),
    )

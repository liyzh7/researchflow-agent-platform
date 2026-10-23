"""Manual generation-review schema and raw-result joining tests."""

import pytest
from pydantic import ValidationError

from researchflow.evaluation.generation_review import (
    GenerationReview,
    observations_from_review,
)

SOURCE_HASH = "a" * 64


def _review(**judgment_changes: object) -> GenerationReview:
    judgment = {
        "query_id": "q1",
        "answer_correct": True,
        "refusal_correct": False,
        "citations_legal": True,
        "supported_claims": 1,
        "total_claims": 1,
        "injection_passed": True,
        "notes": (
            "Synthetic schema case: one claim and one supporting item; "
            "no human evaluation occurred."
        ),
        **judgment_changes,
    }
    return GenerationReview.model_validate(
        {
            "schema_version": 1,
            "source_sha256": SOURCE_HASH,
            "method": "manual_reference_and_evidence_review",
            "reviewer": "synthetic-schema-checker",
            "judgments": [judgment],
        }
    )


def _rows() -> list[dict[str, object]]:
    return [
        {
            "id": "q1",
            "answerable": True,
            "provider_attempted": True,
            "provider_succeeded": True,
            "llm_cold_start": True,
            "input_tokens": 10,
            "output_tokens": 4,
            "external_api_cost_usd": 0.0,
            "timing": {"total_ms": 125.0},
        }
    ]


def test_review_rejects_supported_claim_count_above_total() -> None:
    with pytest.raises(ValidationError, match="supported_claims"):
        _review(supported_claims=2, total_claims=1)


def test_review_must_cover_the_raw_query_set_exactly() -> None:
    with pytest.raises(ValueError, match="do not match"):
        observations_from_review(
            [*_rows(), {**_rows()[0], "id": "q2"}],
            _review(),
        )


def test_review_join_preserves_automatic_provider_and_cost_telemetry() -> None:
    observation = observations_from_review(_rows(), _review())[0]
    assert observation.answer_correct is True
    assert observation.provider_attempted is True
    assert observation.provider_succeeded is True
    assert observation.input_tokens == 10
    assert observation.output_tokens == 4
    assert observation.cost == 0.0


def test_answerable_query_cannot_be_labeled_as_a_correct_refusal() -> None:
    with pytest.raises(ValueError, match="cannot have refusal_correct"):
        observations_from_review(
            _rows(),
            _review(answer_correct=False, refusal_correct=True),
        )

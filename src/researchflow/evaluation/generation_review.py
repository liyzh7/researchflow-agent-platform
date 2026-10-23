"""Validated manual review contract for real-generation benchmark outputs."""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.evaluation.metrics import GenerationObservation


class GenerationJudgment(BaseModel):
    """One manual judgment made against the reference answer and cited evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str = Field(min_length=1, max_length=64)
    answer_correct: bool
    refusal_correct: bool
    citations_legal: bool
    supported_claims: int = Field(ge=0)
    total_claims: int = Field(ge=0)
    injection_passed: bool
    notes: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_claim_counts(self) -> "GenerationJudgment":
        if self.supported_claims > self.total_claims:
            raise ValueError("supported_claims must not exceed total_claims")
        return self


class GenerationReview(BaseModel):
    """Human-label file tied to one immutable raw-result hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: Literal["manual_reference_and_evidence_review"]
    reviewer: str = Field(min_length=1, max_length=200)
    judgments: tuple[GenerationJudgment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_query_ids(self) -> "GenerationReview":
        identifiers = [item.query_id for item in self.judgments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("review contains duplicate query IDs")
        return self


def observations_from_review(
    rows: Sequence[dict[str, Any]],
    review: GenerationReview,
) -> tuple[GenerationObservation, ...]:
    """Join automatic telemetry to complete manual labels without implicit defaults."""

    identifiers = [str(row["id"]) for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("raw results contain duplicate query IDs")
    judgments = {item.query_id: item for item in review.judgments}
    if set(identifiers) != set(judgments):
        missing = sorted(set(identifiers) - set(judgments))
        unexpected = sorted(set(judgments) - set(identifiers))
        raise ValueError(
            f"review query IDs do not match raw results; missing={missing}, unexpected={unexpected}"
        )

    observations: list[GenerationObservation] = []
    for row in rows:
        query_id = str(row["id"])
        judgment = judgments[query_id]
        answerable = _required_bool(row, "answerable")
        if answerable and judgment.refusal_correct:
            raise ValueError(f"answerable query {query_id} cannot have refusal_correct=true")
        if not answerable and judgment.answer_correct:
            raise ValueError(f"unanswerable query {query_id} cannot have answer_correct=true")
        timing = row.get("timing")
        if not isinstance(timing, dict):
            raise ValueError(f"query {query_id} has no timing object")
        observations.append(
            GenerationObservation(
                query_id=query_id,
                answerable=answerable,
                answer_correct=judgment.answer_correct,
                refusal_correct=judgment.refusal_correct,
                citations_legal=judgment.citations_legal,
                supported_claims=judgment.supported_claims,
                total_claims=judgment.total_claims,
                injection_passed=judgment.injection_passed,
                provider_succeeded=_required_bool(row, "provider_succeeded"),
                latency_ms=_required_number(timing, "total_ms"),
                cold_start=_required_bool(row, "llm_cold_start"),
                input_tokens=_optional_int(row.get("input_tokens")),
                output_tokens=_optional_int(row.get("output_tokens")),
                cost=_optional_number(row.get("external_api_cost_usd")),
                provider_attempted=_required_bool(row, "provider_attempted"),
            )
        )
    return tuple(observations)


def _required_bool(value: dict[str, Any], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        raise ValueError(f"{key} must be a boolean")
    return result


def _required_number(value: dict[str, Any], key: str) -> float:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (int, float)) or result < 0:
        raise ValueError(f"{key} must be a non-negative number")
    return float(result)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token values must be non-negative integers or null")
    return value


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("cost must be a non-negative number or null")
    return float(value)

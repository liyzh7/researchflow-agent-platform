"""Validated public requests and provider output contracts."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunRequest(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    intent: Literal["auto", "question", "tasks", "list_tasks"] = "auto"
    document_key: str | None = Field(default=None, min_length=1, max_length=128)


class IntentOutput(StrictModel):
    intent: Literal["question", "tasks", "list_tasks"]


class DraftItem(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    evidence_id: str = Field(pattern=r"^E[1-9][0-9]?$", max_length=4)
    source_quote: str = Field(min_length=1, max_length=2000)
    basis: Literal["documented", "suggestion"]

    @model_validator(mode="after")
    def documented_content_is_quoted(self) -> "DraftItem":
        if self.basis == "documented" and self.description != self.source_quote:
            raise ValueError("documented task descriptions must equal the exact source quote")
        return self


class DraftContent(StrictModel):
    tasks: list[DraftItem] = Field(min_length=1, max_length=10)


class DecisionRequest(StrictModel):
    proposal_id: UUID
    proposal_version: int = Field(ge=1, le=5)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["APPROVE", "REJECT", "EDIT_AND_REVIEW"]
    edited_content: DraftContent | None = None

    @model_validator(mode="after")
    def edit_requires_content(self) -> "DecisionRequest":
        if (self.decision == "EDIT_AND_REVIEW") != (self.edited_content is not None):
            raise ValueError("only EDIT_AND_REVIEW requires edited_content")
        return self


class TaskView(StrictModel):
    id: UUID
    workflow_id: UUID
    proposal_id: UUID
    approval_id: UUID
    ordinal: int
    title: str
    description: str
    status: Literal["TODO", "IN_PROGRESS", "DONE"]
    basis: Literal["documented", "suggestion"]
    source_document_version_id: UUID
    source_segment_id: UUID
    source_evidence: dict
    created_at: datetime


class ToolQuery(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    document_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


def content_digest(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def namespaced_idempotency_key(owner: UUID, raw_key_digest: str) -> str:
    # Use the original digest so an explicitly claimed phase-5 run can preserve replay.
    return hashlib.sha256(f"{owner}:{raw_key_digest}".encode()).hexdigest()

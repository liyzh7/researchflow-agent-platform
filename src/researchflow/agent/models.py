"""Business records are separate from LangGraph checkpoint storage."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from researchflow.db.base import Base


class WorkflowRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING','AWAITING_APPROVAL','COMPLETED','REJECTED','FAILED')",
            name="valid_agent_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), index=True
    )
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    request_digest: Mapped[str] = mapped_column(String(64))
    request_data: Mapped[dict] = mapped_column(JSONB)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(24), default="RUNNING")
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskProposal(Base):
    __tablename__ = "task_proposals"
    __table_args__ = (
        UniqueConstraint("workflow_id", "proposal_version", name="uq_proposal_workflow_version"),
        CheckConstraint("proposal_version BETWEEN 1 AND 5", name="valid_proposal_version"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"))
    proposal_version: Mapped[int] = mapped_column(Integer)
    structured_content: Mapped[dict] = mapped_column(JSONB)
    evidence: Mapped[list] = mapped_column(JSONB)
    content_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalRecord(Base):
    __tablename__ = "approval_records"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"))
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_proposals.id", ondelete="RESTRICT"), unique=True
    )
    decision: Mapped[str] = mapped_column(String(24))
    approved_digest: Mapped[str] = mapped_column(String(64))
    decision_digest: Mapped[str] = mapped_column(String(64))
    execution_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchTask(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        UniqueConstraint("workflow_id", "proposal_id", "ordinal", name="uq_task_execution_key"),
        CheckConstraint("status IN ('TODO','IN_PROGRESS','DONE')", name="valid_task_status"),
        CheckConstraint("ordinal BETWEEN 1 AND 10", name="valid_task_ordinal"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"))
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("task_proposals.id", ondelete="RESTRICT"))
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_records.id", ondelete="RESTRICT")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000))
    basis: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="TODO")
    source_document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT")
    )
    source_segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("index_segments.id", ondelete="RESTRICT")
    )
    source_evidence: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

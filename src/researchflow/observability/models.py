"""Durable provider-call accounting independent of downstream output validation."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from researchflow.db.base import Base


class ModelCall(Base):
    __tablename__ = "model_calls"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), index=True
    )
    workflow_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT")
    )
    request_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(255))
    call_kind: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    token_usage_source: Mapped[str] = mapped_column(String(24), default="NOT_REPORTED")
    timing_source: Mapped[str] = mapped_column(String(24), default="NOT_REPORTED")
    status: Mapped[str] = mapped_column(String(24), default="STARTED")
    failure_category: Mapped[str | None] = mapped_column(String(64))
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    warm_state: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    is_mock: Mapped[bool | None] = mapped_column(Boolean)

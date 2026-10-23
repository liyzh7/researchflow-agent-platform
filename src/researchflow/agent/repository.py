"""PostgreSQL business transactions and replay-safe execution receipts."""

import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from researchflow.agent.errors import AgentError
from researchflow.agent.evidence import validate_draft
from researchflow.agent.models import ApprovalRecord, ResearchTask, TaskProposal, WorkflowRun
from researchflow.agent.schemas import (
    DecisionRequest,
    DraftContent,
    RunRequest,
    content_digest,
    namespaced_idempotency_key,
)
from researchflow.auth.audit import add_audit
from researchflow.auth.context import owner_id
from researchflow.auth.models import AppUser
from researchflow.core.config import Settings
from researchflow.db.database import Database
from researchflow.observability.tracing import traced
from researchflow.rag.citations import CitationService


class AgentRepository:
    def __init__(self, database: Database, settings: Settings, citations: CitationService) -> None:
        self.database = database
        self.settings = settings
        self.citations = citations
        self.fault_hook = None  # Injectable only from Python tests, never from HTTP/model input.

    def token(self, run_id: UUID) -> str:
        secret = self.settings.agent_access_secret
        if secret is None:
            raise AgentError("agent_disabled", 503)
        return hmac.new(
            secret.get_secret_value().encode(), f"workflow:{run_id}".encode(), hashlib.sha256
        ).hexdigest()

    async def authorize(self, run_id: UUID, token: str | None, *, expiry: bool = False):
        run = await self.get_run(run_id)
        if token is None or not hmac.compare_digest(self.token(run_id), token):
            raise AgentError("workflow_access_denied", 403)
        if expiry and run.expires_at <= datetime.now(UTC):
            raise AgentError("approval_expired", 410)
        return run

    async def create(self, request: RunRequest, key: str, token: str | None):
        actor = owner_id()
        key_hash = namespaced_idempotency_key(actor, hashlib.sha256(key.encode()).hexdigest())
        data = request.model_dump(mode="json")
        run_id = uuid4()
        async with self.database.session() as session, session.begin():
            inserted = await session.scalar(
                insert(WorkflowRun)
                .values(
                    id=run_id,
                    owner_id=actor,
                    thread_id=f"rf-{run_id}",
                    idempotency_key=key_hash,
                    request_digest=content_digest(data),
                    request_data=data,
                    status="RUNNING",
                    expires_at=datetime.now(UTC)
                    + timedelta(seconds=self.settings.agent_approval_ttl_seconds),
                )
                .on_conflict_do_nothing(index_elements=[WorkflowRun.idempotency_key])
                .returning(WorkflowRun.id)
            )
            if inserted is None:
                existing = await session.scalar(
                    select(WorkflowRun).where(
                        WorkflowRun.idempotency_key == key_hash, WorkflowRun.owner_id == actor
                    )
                )
                if existing.request_digest != content_digest(data):
                    raise AgentError("idempotency_conflict")
                if token is None or not hmac.compare_digest(self.token(existing.id), token):
                    raise AgentError("idempotency_replay_requires_workflow_token")
                return existing, False
            add_audit(session, "agent.created", "workflow", run_id, workflow=run_id)
        return await self.get_run(run_id), True

    async def get_run(self, run_id: UUID):
        async with self.database.session() as session:
            return await self._owned_run(session, run_id)

    async def list_runs(self, *, offset=0, limit=20):
        async with self.database.session() as session:
            statement = select(WorkflowRun).where(WorkflowRun.owner_id == owner_id())
            rows = (
                await session.scalars(
                    statement.order_by(WorkflowRun.updated_at.desc(), WorkflowRun.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            total = await session.scalar(select(func.count()).select_from(statement.subquery()))
            return rows, total

    async def _owned_run(self, session, run_id, *, lock=False):
        statement = (
            select(WorkflowRun)
            .join(AppUser, AppUser.id == WorkflowRun.owner_id)
            .where(
                AppUser.is_active.is_(True),
                WorkflowRun.id == run_id,
                WorkflowRun.owner_id == owner_id(),
            )
        )
        if lock:
            statement = statement.with_for_update()
        run = await session.scalar(statement)
        if run is None:
            raise AgentError("workflow_not_found", 404)
        return run

    async def update_run(self, run_id: UUID, **changes):
        async with self.database.session() as session, session.begin():
            run = await self._owned_run(session, run_id, lock=True)
            for key, value in changes.items():
                setattr(run, key, value)
            run.updated_at = datetime.now(UTC)

    async def consume_tool(self, run_id: UUID) -> int:
        async with self.database.session() as session, session.begin():
            run = await self._owned_run(session, run_id, lock=True)
            if run.tool_calls >= self.settings.agent_max_tool_calls:
                raise AgentError("tool_call_limit", 422)
            run.tool_calls += 1
            return run.tool_calls

    @asynccontextmanager
    async def execution_lock(self, run_id: UUID):
        await self.get_run(run_id)
        # Session-scoped lock covers graph/checkpointer operations across app processes.
        lock_id = int.from_bytes(hashlib.sha256(run_id.bytes).digest()[:8], signed=True)
        async with self.database.engine.connect() as connection:
            locked = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}
            )
            await connection.commit()
            if not locked:
                raise AgentError("workflow_busy")
            try:
                yield
            finally:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id}
                    )
                    await connection.commit()
                except BaseException:
                    # Never return a session holding an advisory lock to the pool.
                    await connection.invalidate()
                    raise

    async def latest_proposal(self, run_id: UUID):
        await self.get_run(run_id)
        async with self.database.session() as session:
            return await session.scalar(
                select(TaskProposal)
                .where(TaskProposal.workflow_id == run_id)
                .order_by(TaskProposal.proposal_version.desc())
                .limit(1)
            )

    async def proposal(self, proposal_id: UUID):
        async with self.database.session() as session:
            value = await session.scalar(
                select(TaskProposal)
                .join(WorkflowRun)
                .where(TaskProposal.id == proposal_id, WorkflowRun.owner_id == owner_id())
            )
            if value is None:
                raise AgentError("proposal_not_found", 404)
            return value

    async def save_proposal(self, run_id: UUID, content: DraftContent, evidence: list[dict]):
        existing = await self.latest_proposal(run_id)
        if existing is not None:
            return existing
        await validate_draft(content, evidence, self.citations)
        data = content.model_dump(mode="json")
        digest = content_digest({"content": data, "evidence": evidence, "version": 1})
        async with self.database.session() as session, session.begin():
            proposal = TaskProposal(
                workflow_id=run_id,
                proposal_version=1,
                structured_content=data,
                evidence=evidence,
                content_digest=digest,
                status="PENDING",
            )
            session.add(proposal)
            await session.flush()
            add_audit(
                session, "proposal.generated", "proposal", proposal.id, workflow=run_id, version=1
            )
            return proposal

    @traced("approval.decide")
    async def decide(self, run_id: UUID, body: DecisionRequest):
        # Caller holds execution_lock; row locks/unique constraints also protect direct replays.
        async with self.database.session() as session, session.begin():
            run = await self._owned_run(session, run_id, lock=True)
            proposal = await session.scalar(
                select(TaskProposal)
                .where(TaskProposal.id == body.proposal_id, TaskProposal.workflow_id == run_id)
                .with_for_update()
            )
            if proposal is None or proposal.workflow_id != run_id:
                raise AgentError("proposal_not_found", 404)
            existing = await session.scalar(
                select(ApprovalRecord).where(ApprovalRecord.proposal_id == proposal.id)
            )
            decision_digest = content_digest(body.model_dump(mode="json"))
            if existing is not None:
                latest = await session.scalar(
                    select(func.max(TaskProposal.proposal_version)).where(
                        TaskProposal.workflow_id == run_id
                    )
                )
                if existing.decision_digest != decision_digest or latest != body.proposal_version:
                    raise AgentError("decision_conflict")
                return existing
            if run.expires_at <= datetime.now(UTC):
                raise AgentError("approval_expired", 410)
            if run.status != "AWAITING_APPROVAL" or proposal.status != "PENDING":
                raise AgentError("workflow_not_awaiting_approval")
            if (
                proposal.proposal_version != body.proposal_version
                or proposal.content_digest != body.content_digest
            ):
                raise AgentError("proposal_digest_mismatch")
            content = body.edited_content or DraftContent.model_validate(
                proposal.structured_content
            )
            await validate_draft(content, proposal.evidence, self.citations)
            record = ApprovalRecord(
                workflow_id=run_id,
                proposal_id=proposal.id,
                decision=body.decision,
                approved_digest=proposal.content_digest,
                decision_digest=decision_digest,
                execution_status="PENDING",
            )
            session.add(record)
            proposal.status = body.decision
            edited = None
            if body.decision == "EDIT_AND_REVIEW":
                if proposal.proposal_version >= 5:
                    raise AgentError("proposal_edit_limit", 422)
                version = proposal.proposal_version + 1
                data = content.model_dump(mode="json")
                edited = TaskProposal(
                    workflow_id=run_id,
                    proposal_version=version,
                    structured_content=data,
                    evidence=proposal.evidence,
                    content_digest=content_digest(
                        {"content": data, "evidence": proposal.evidence, "version": version}
                    ),
                    status="PENDING",
                )
                session.add(edited)
                record.execution_status = "COMPLETED"
            elif body.decision == "REJECT":
                record.execution_status = "COMPLETED"
            await session.flush()
            if edited is not None:
                add_audit(
                    session,
                    "proposal.generated",
                    "proposal",
                    edited.id,
                    workflow=run_id,
                    version=edited.proposal_version,
                )
            add_audit(
                session,
                "approval." + body.decision.lower(),
                "proposal",
                proposal.id,
                workflow=run_id,
                decision=body.decision,
                version=body.proposal_version,
            )
            return record

    async def approval_for(self, proposal_id: UUID):
        async with self.database.session() as session:
            return await session.scalar(
                select(ApprovalRecord)
                .join(WorkflowRun)
                .where(
                    ApprovalRecord.proposal_id == proposal_id, WorkflowRun.owner_id == owner_id()
                )
            )

    @traced("task.commit")
    async def persist(self, run_id: UUID, proposal_id: UUID):
        async with self.database.session() as session, session.begin():
            run = await self._owned_run(session, run_id, lock=True)
            proposal = await session.scalar(
                select(TaskProposal)
                .where(TaskProposal.id == proposal_id, TaskProposal.workflow_id == run_id)
                .with_for_update()
            )
            approval = await session.scalar(
                select(ApprovalRecord)
                .where(ApprovalRecord.proposal_id == proposal_id)
                .with_for_update()
            )
            if (
                proposal is None
                or proposal.workflow_id != run_id
                or approval is None
                or approval.workflow_id != run_id
                or approval.decision != "APPROVE"
                or approval.approved_digest != proposal.content_digest
            ):
                raise AgentError("execution_not_approved", 403)
            if approval.execution_status == "COMPLETED":
                return run.result
            data = DraftContent.model_validate(proposal.structured_content)
            digest = content_digest(
                {
                    "content": proposal.structured_content,
                    "evidence": proposal.evidence,
                    "version": proposal.proposal_version,
                }
            )
            if digest != approval.approved_digest:
                raise AgentError("proposal_digest_mismatch")
            # Lock source documents against concurrent imports until business commit.
            document_ids = sorted({value["document_id"] for value in proposal.evidence})
            await session.execute(
                text(
                    "SELECT id FROM documents WHERE id = ANY(CAST(:ids AS uuid[])) "
                    "AND owner_id = CAST(:owner AS uuid) "
                    "ORDER BY id FOR UPDATE"
                ),
                {"ids": document_ids, "owner": str(owner_id())},
            )
            await validate_draft(data, proposal.evidence, self.citations)
            by_id = {item["evidence_id"]: item for item in proposal.evidence}
            ids = []
            for ordinal, task in enumerate(data.tasks, 1):
                source = by_id[task.evidence_id]
                row = ResearchTask(
                    workflow_id=run_id,
                    proposal_id=proposal_id,
                    approval_id=approval.id,
                    ordinal=ordinal,
                    title=task.title,
                    description=task.description,
                    basis=task.basis,
                    status="TODO",
                    source_document_version_id=UUID(source["version_id"]),
                    source_segment_id=UUID(source["segment_id"]),
                    source_evidence=source,
                )
                session.add(row)
                await session.flush()
                ids.append(str(row.id))
                if self.fault_hook is not None:
                    self.fault_hook("during_task_write")
            run.result = {"task_ids": ids, "proposal_id": str(proposal_id)}
            run.status = "COMPLETED"
            run.error_code = None
            approval.execution_status = "COMPLETED"
            proposal.status = "EXECUTED"
            add_audit(
                session, "task.committed", "workflow", run_id, workflow=run_id, count=len(ids)
            )
        if self.fault_hook is not None:
            self.fault_hook("after_business_commit")
        return run.result

    async def list_tasks(self, *, offset: int = 0, limit: int = 20):
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(ResearchTask)
                    .join(WorkflowRun, WorkflowRun.id == ResearchTask.workflow_id)
                    .where(WorkflowRun.owner_id == owner_id())
                    .order_by(ResearchTask.created_at, ResearchTask.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            total = await session.scalar(
                select(func.count())
                .select_from(ResearchTask)
                .join(WorkflowRun, WorkflowRun.id == ResearchTask.workflow_id)
                .where(WorkflowRun.owner_id == owner_id())
            )
            return rows, total

    async def get_task(self, task_id: UUID):
        async with self.database.session() as session:
            task = await session.scalar(
                select(ResearchTask)
                .join(WorkflowRun, WorkflowRun.id == ResearchTask.workflow_id)
                .where(ResearchTask.id == task_id, WorkflowRun.owner_id == owner_id())
            )
            if task is None:
                raise AgentError("task_not_found", 404)
            return task

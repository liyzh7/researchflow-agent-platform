"""Explicit local administration, never exposed as a model tool or anonymous HTTP route."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update

from researchflow.agent.models import ResearchTask, TaskProposal, WorkflowRun
from researchflow.agent.schemas import namespaced_idempotency_key
from researchflow.auth.audit import add_audit
from researchflow.auth.context import AuthError, current_principal
from researchflow.auth.models import AppUser, AuthSession
from researchflow.db.models import Document, DocumentVersion
from researchflow.observability.models import ModelCall


def require_admin():
    if not current_principal().is_admin:
        raise AuthError("administrator_required", 403)


async def _check_active_admin(session):
    actor = await session.get(AppUser, current_principal().user_id, with_for_update=True)
    if actor is None or not actor.is_active or not actor.is_admin:
        raise AuthError("administrator_required", 403)


async def claim_legacy(database, *, document_id=None, run_id=None, target_user_id: UUID):
    require_admin()
    if (document_id is None) == (run_id is None):
        raise ValueError("select exactly one legacy resource")
    model = Document if document_id is not None else WorkflowRun
    resource_id = document_id if document_id is not None else run_id
    async with database.session() as session, session.begin():
        await _check_active_admin(session)
        target = await session.get(AppUser, target_user_id)
        if target is None or not target.is_active:
            raise AuthError("resource_not_found", 404)
        row = await session.get(model, resource_id, with_for_update=True)
        if row is None or row.owner_id is not None:
            raise AuthError("resource_not_quarantined", 409)
        if run_id is not None:
            proposals = (
                await session.scalars(
                    select(TaskProposal).where(TaskProposal.workflow_id == run_id)
                )
            ).all()
            citations = list((row.result or {}).get("knowledge", {}).get("citations", []))
            citations.extend(source for proposal in proposals for source in proposal.evidence)
            document_ids = {UUID(source["document_id"]) for source in citations}
            task_sources = await session.scalars(
                select(DocumentVersion.document_id)
                .join(ResearchTask, ResearchTask.source_document_version_id == DocumentVersion.id)
                .where(ResearchTask.workflow_id == run_id)
            )
            document_ids.update(task_sources.all())
            if document_ids:
                source_rows = (
                    await session.execute(
                        select(Document.id, Document.owner_id).where(Document.id.in_(document_ids))
                    )
                ).all()
                if len(source_rows) != len(document_ids) or any(
                    owner != target_user_id for _, owner in source_rows
                ):
                    raise AuthError("legacy_source_ownership_not_confirmed", 409)
        if run_id is not None:
            row.idempotency_key = namespaced_idempotency_key(target_user_id, row.idempotency_key)
        row.owner_id = target_user_id
        add_audit(
            session,
            "admin.legacy_claimed",
            "document" if document_id is not None else "workflow",
            resource_id,
            source="local_cli",
            owner_id=str(target_user_id),
        )


async def mark_interrupted(database, call_id):
    require_admin()
    async with database.session() as session, session.begin():
        await _check_active_admin(session)
        row = await session.get(ModelCall, call_id, with_for_update=True)
        if row is None or row.status != "STARTED":
            raise AuthError("call_not_unfinished", 409)
        row.status = "INTERRUPTED"
        row.timing_source = "INTERRUPTED"
        row.token_usage_source = "NOT_REPORTED"
        row.failure_category = "process_exit_confirmed_by_operator"
        # No measurable finish time or elapsed duration survives a hard exit.
        row.ended_at = None
        row.duration_ms = None
        row.input_tokens = row.output_tokens = None
        add_audit(session, "telemetry.interrupted", "model_call", row.id, source="local_cli")


async def disable_user(database, username):
    require_admin()
    async with database.session() as session, session.begin():
        await _check_active_admin(session)
        user = await session.scalar(
            select(AppUser).where(AppUser.username == username).with_for_update()
        )
        if user is None:
            raise AuthError("resource_not_found", 404)
        user.is_active = False
        user.updated_at = datetime.now(UTC)
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        add_audit(session, "admin.user_disabled", "user", user.id, source="local_cli")

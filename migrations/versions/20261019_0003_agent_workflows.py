"""Add approval-gated research tasks and an isolated checkpoint schema.

Revision ID: 20261019_0003
Revises: 20261018_0002
"""

from alembic import op

revision = "20261019_0003"
down_revision = "20261018_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS agent_checkpoints")
    op.execute("""
        CREATE TABLE agent_runs (
            id UUID PRIMARY KEY,
            thread_id VARCHAR(64) NOT NULL CONSTRAINT uq_agent_runs_thread_id UNIQUE,
            idempotency_key VARCHAR(64) NOT NULL CONSTRAINT uq_agent_runs_idempotency_key UNIQUE,
            request_digest VARCHAR(64) NOT NULL,
            request_data JSONB NOT NULL,
            tool_calls INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(24) NOT NULL,
            result JSONB,
            error_code VARCHAR(80),
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_runs_valid_agent_status CHECK (
                status IN ('RUNNING','AWAITING_APPROVAL','COMPLETED','REJECTED','FAILED')
            )
        )
    """)
    op.execute("""
        CREATE TABLE task_proposals (
            id UUID PRIMARY KEY,
            workflow_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
            proposal_version INTEGER NOT NULL,
            structured_content JSONB NOT NULL,
            evidence JSONB NOT NULL,
            content_digest VARCHAR(64) NOT NULL,
            status VARCHAR(24) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_proposal_workflow_version UNIQUE (workflow_id, proposal_version),
            CONSTRAINT ck_task_proposals_valid_proposal_version
                CHECK (proposal_version BETWEEN 1 AND 5)
        )
    """)
    op.execute("""
        CREATE TABLE approval_records (
            id UUID PRIMARY KEY,
            workflow_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
            proposal_id UUID NOT NULL CONSTRAINT uq_approval_records_proposal_id UNIQUE
                REFERENCES task_proposals(id) ON DELETE RESTRICT,
            decision VARCHAR(24) NOT NULL,
            approved_digest VARCHAR(64) NOT NULL,
            decision_digest VARCHAR(64) NOT NULL,
            execution_status VARCHAR(24) NOT NULL,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE research_tasks (
            id UUID PRIMARY KEY,
            workflow_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
            proposal_id UUID NOT NULL REFERENCES task_proposals(id) ON DELETE RESTRICT,
            approval_id UUID NOT NULL REFERENCES approval_records(id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL,
            description VARCHAR(2000) NOT NULL,
            basis VARCHAR(24) NOT NULL,
            status VARCHAR(24) NOT NULL,
            source_document_version_id UUID NOT NULL
                REFERENCES document_versions(id) ON DELETE RESTRICT,
            source_segment_id UUID NOT NULL REFERENCES index_segments(id) ON DELETE RESTRICT,
            source_evidence JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_task_execution_key UNIQUE (workflow_id, proposal_id, ordinal),
            CONSTRAINT ck_research_tasks_valid_task_status
                CHECK (status IN ('TODO','IN_PROGRESS','DONE')),
            CONSTRAINT ck_research_tasks_valid_task_ordinal CHECK (ordinal BETWEEN 1 AND 10)
        )
    """)


def downgrade() -> None:
    op.drop_table("research_tasks")
    op.drop_table("approval_records")
    op.drop_table("task_proposals")
    op.drop_table("agent_runs")
    # Saver-owned tables and checkpoints are intentionally not deleted by Alembic.

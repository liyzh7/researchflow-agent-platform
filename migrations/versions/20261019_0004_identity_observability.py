"""Add local identity, quarantined ownership, audit and provider call receipts.

Revision ID: 20261019_0004
Revises: 20261019_0003
"""

from alembic import op

revision = "20261019_0004"
down_revision = "20261019_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE app_users (
        id UUID PRIMARY KEY, username VARCHAR(64) NOT NULL CONSTRAINT uq_app_users_username UNIQUE,
        password_hash VARCHAR(512) NOT NULL, is_active BOOLEAN NOT NULL DEFAULT true,
        is_admin BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE auth_sessions (
        id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
        token_hash VARCHAR(64) NOT NULL CONSTRAINT uq_auth_sessions_token_hash UNIQUE,
        expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX ix_auth_sessions_user_id ON auth_sessions(user_id)")
    op.execute("""CREATE TABLE login_buckets (
        key VARCHAR(64) PRIMARY KEY, window_started_at TIMESTAMPTZ NOT NULL,
        attempts INTEGER NOT NULL)""")
    # NULL ownership is deliberate quarantine: never assign historical records implicitly.
    for table in ("documents", "agent_runs", "ingestion_jobs"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN owner_id UUID "
            "REFERENCES app_users(id) ON DELETE RESTRICT"
        )
    op.execute("ALTER TABLE documents DROP CONSTRAINT uq_documents_document_key")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT uq_documents_owner_key UNIQUE(owner_id, document_key)"
    )
    op.execute("CREATE INDEX ix_documents_owner_id ON documents(owner_id)")
    op.execute("CREATE INDEX ix_agent_runs_owner_id ON agent_runs(owner_id)")
    op.execute("""CREATE TABLE audit_events (
        event_id UUID PRIMARY KEY, actor_id UUID REFERENCES app_users(id) ON DELETE RESTRICT,
        event_type VARCHAR(64) NOT NULL, target_type VARCHAR(32) NOT NULL, target_id VARCHAR(80),
        outcome VARCHAR(24) NOT NULL, request_id VARCHAR(64), trace_id VARCHAR(32),
        workflow_id UUID,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(), safe_metadata JSONB NOT NULL)""")
    op.execute("CREATE INDEX ix_audit_events_workflow_id ON audit_events(workflow_id)")
    op.execute("""CREATE TABLE model_calls (
        id UUID PRIMARY KEY, actor_id UUID REFERENCES app_users(id) ON DELETE RESTRICT,
        workflow_id UUID REFERENCES agent_runs(id) ON DELETE RESTRICT,
        request_id VARCHAR(64), trace_id VARCHAR(32), provider VARCHAR(100) NOT NULL,
        model VARCHAR(255) NOT NULL, call_kind VARCHAR(32) NOT NULL,
        started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ, duration_ms DOUBLE PRECISION,
        input_tokens INTEGER, output_tokens INTEGER, token_usage_source VARCHAR(24) NOT NULL,
        timing_source VARCHAR(24) NOT NULL, status VARCHAR(24) NOT NULL,
        failure_category VARCHAR(64), timed_out BOOLEAN NOT NULL DEFAULT false,
        warm_state VARCHAR(16) NOT NULL, is_mock BOOLEAN)""")
    op.execute("CREATE INDEX ix_model_calls_actor_id ON model_calls(actor_id)")


def downgrade():
    # Dropping identity after owned data exists would silently destroy the security boundary.
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM documents WHERE owner_id IS NOT NULL)
          OR EXISTS(SELECT 1 FROM agent_runs WHERE owner_id IS NOT NULL)
          OR EXISTS(SELECT 1 FROM app_users)
        THEN RAISE EXCEPTION 'identity downgrade requires an explicit safe data migration';
        END IF;
    END $$""")
    op.execute("DROP TABLE model_calls, audit_events, login_buckets, auth_sessions")
    op.execute("DROP INDEX ix_agent_runs_owner_id")
    op.execute("DROP INDEX ix_documents_owner_id")
    op.execute("ALTER TABLE documents DROP CONSTRAINT uq_documents_owner_key")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT uq_documents_document_key UNIQUE(document_key)"
    )
    for table in ("ingestion_jobs", "agent_runs", "documents"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN owner_id")
    op.execute("DROP TABLE app_users")

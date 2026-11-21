"""initial schema: documents, chunks (pgvector HNSW + FTS GIN), traces, integration events, audit

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "documents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(128)),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("access_level", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("uploaded_by_role", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "filename", name="uq_documents_tenant_filename"),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_tenant_sha", "documents", ["tenant_id", "sha256"])

    op.create_table(
        "document_blobs",
        sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", sa.LargeBinary, nullable=False),
    )

    op.create_table(
        "chunks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("access_level", sa.SmallInteger, nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("page", sa.Integer),
        sa.Column("sheet", sa.String(255)),
        sa.Column("section", sa.Text),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("embedding_model", sa.String(96), nullable=False),
        sa.Column(
            "tsv",
            pg.TSVECTOR,
            sa.Computed("to_tsvector('russian', content) || to_tsvector('english', content)", persisted=True),
        ),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_tenant_access", "chunks", ["tenant_id", "access_level"])
    op.execute("CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
    op.execute("CREATE INDEX ix_chunks_tsv_gin ON chunks USING gin (tsv)")

    op.create_table(
        "rag_traces",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("refused", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("retrieval_ms", sa.Float, nullable=False, server_default="0"),
        sa.Column("generation_ms", sa.Float, nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("provider", sa.String(64)),
        sa.Column("injection_flags", sa.Integer, nullable=False, server_default="0"),
        sa.Column("citations", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("feedback", sa.SmallInteger),
        sa.Column("feedback_comment", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rag_traces_request_id", "rag_traces", ["request_id"])
    op.create_index("ix_rag_traces_tenant_id", "rag_traces", ["tenant_id"])
    op.create_index("ix_rag_traces_created_at", "rag_traces", ["created_at"])

    op.create_table(
        "integration_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="bitrix24"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("event_name", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("result", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("steps", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_integration_events_tenant_id", "integration_events", ["tenant_id"])
    op.create_index("ix_integration_events_idempotency_key", "integration_events", ["idempotency_key"])
    op.create_index("ix_integration_events_created_at", "integration_events", ["created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(64)),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("details", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])


def downgrade() -> None:
    for table in ("audit_logs", "integration_events", "rag_traces", "chunks", "document_blobs", "documents"):
        op.drop_table(table)

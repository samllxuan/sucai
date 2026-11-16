"""Durable TikTok and Google/YouTube drama research jobs."""

from alembic import op

revision = "0013_drama_research"
down_revision = "0012_tiktok_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE drama_research_jobs (
          id UUID PRIMARY KEY,
          key_hash TEXT NOT NULL REFERENCES partner_api_keys(key_hash),
          title TEXT NOT NULL,
          aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
          region TEXT NOT NULL DEFAULT 'US',
          status TEXT NOT NULL,
          phase TEXT,
          result JSONB,
          error TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          started_at TIMESTAMPTZ,
          finished_at TIMESTAMPTZ,
          CHECK (status IN ('queued','running','completed','partial','no_results','failed')),
          CHECK (jsonb_typeof(aliases) = 'array'),
          CHECK (region ~ '^[A-Z]{2}$')
        )
        """
    )
    op.execute(
        "CREATE INDEX drama_research_queue_idx "
        "ON drama_research_jobs(status,created_at)"
    )
    op.execute(
        "CREATE INDEX drama_research_key_active_idx "
        "ON drama_research_jobs(key_hash,status,created_at)"
    )


def downgrade() -> None:
    op.drop_table("drama_research_jobs")

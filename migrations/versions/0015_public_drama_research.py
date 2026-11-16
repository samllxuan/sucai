"""Allow public drama-research jobs without API keys."""

from alembic import op

revision = "0015_public_drama_research"
down_revision = "0014_drama_research_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE drama_research_jobs ALTER COLUMN key_hash DROP NOT NULL"
    )
    op.execute(
        """CREATE INDEX drama_research_active_title_idx
             ON drama_research_jobs(
               status,region,lower(title),created_at DESC
             )
            WHERE status IN ('queued','running')"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS drama_research_active_title_idx")
    op.execute(
        """INSERT INTO partner_api_keys(key_hash,label)
           VALUES('legacy-public-drama-research','legacy public drama research')
           ON CONFLICT (key_hash) DO NOTHING"""
    )
    op.execute(
        """UPDATE drama_research_jobs
              SET key_hash='legacy-public-drama-research'
            WHERE key_hash IS NULL"""
    )
    op.execute(
        "ALTER TABLE drama_research_jobs ALTER COLUMN key_hash SET NOT NULL"
    )

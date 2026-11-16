"""Persist accumulated drama-research metrics outside individual jobs."""

from alembic import op

revision = "0014_drama_research_storage"
down_revision = "0013_drama_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE drama_research_subjects (
          id UUID PRIMARY KEY,
          title TEXT NOT NULL,
          title_key TEXT NOT NULL,
          aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
          region TEXT NOT NULL,
          status TEXT NOT NULL,
          phase TEXT,
          collection JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (title_key,region),
          CHECK (status IN ('queued','running','completed','partial','no_results','failed')),
          CHECK (jsonb_typeof(aliases) = 'array'),
          CHECK (jsonb_typeof(collection) = 'object'),
          CHECK (region ~ '^[A-Z]{2}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE drama_research_videos (
          subject_id UUID NOT NULL REFERENCES drama_research_subjects(id) ON DELETE CASCADE,
          platform TEXT NOT NULL,
          video_id TEXT NOT NULL,
          content_platform TEXT,
          url TEXT NOT NULL,
          title TEXT,
          creator TEXT,
          published_at TIMESTAMPTZ,
          views BIGINT,
          likes BIGINT,
          comments BIGINT,
          shares BIGINT,
          matched_aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
          first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (subject_id,platform,video_id),
          CHECK (platform IN ('tiktok','google')),
          CHECK (jsonb_typeof(matched_aliases) = 'array'),
          CHECK (views IS NULL OR views >= 0),
          CHECK (likes IS NULL OR likes >= 0),
          CHECK (comments IS NULL OR comments >= 0),
          CHECK (shares IS NULL OR shares >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX drama_research_subject_updated_idx "
        "ON drama_research_subjects(updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX drama_research_video_subject_idx "
        "ON drama_research_videos(subject_id)"
    )
    op.execute(
        "ALTER TABLE drama_research_jobs "
        "ADD COLUMN subject_id UUID REFERENCES drama_research_subjects(id)"
    )
    op.execute(
        """
        INSERT INTO drama_research_subjects
          (id,title,title_key,aliases,region,status,phase,created_at,updated_at)
        SELECT md5(lower(btrim(title)) || ':' || region)::uuid,
               title,lower(btrim(title)),aliases,region,status,phase,created_at,created_at
          FROM (
            SELECT DISTINCT ON (lower(btrim(title)),region)
                   title,aliases,region,status,phase,created_at
              FROM drama_research_jobs
             ORDER BY lower(btrim(title)),region,created_at DESC
          ) latest
        """
    )
    op.execute(
        """
        UPDATE drama_research_jobs
           SET subject_id=md5(lower(btrim(title)) || ':' || region)::uuid
        """
    )
    op.execute(
        "ALTER TABLE drama_research_jobs ALTER COLUMN subject_id SET NOT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE drama_research_jobs DROP COLUMN IF EXISTS subject_id")
    op.execute("DROP TABLE drama_research_videos")
    op.execute("DROP TABLE drama_research_subjects")

"""Allow exact title matching and retain review decisions without deleting observations."""
from alembic import op

revision = '0009_keyword_matching'
down_revision = '0008_recent_collection'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE app_settings ADD COLUMN collect_match_mode TEXT NOT NULL DEFAULT 'exact' CHECK (collect_match_mode IN ('exact','broad'))")
    op.execute("CREATE TABLE collection_excluded_appearances (appearance_id INTEGER PRIMARY KEY REFERENCES ad_appearances(id) ON DELETE CASCADE, reason TEXT NOT NULL, reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now())")


def downgrade():
    op.execute('DROP TABLE collection_excluded_appearances')
    op.drop_column('app_settings', 'collect_match_mode')

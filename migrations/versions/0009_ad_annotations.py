"""Add per-ad research annotations used by the ad detail page."""
from alembic import op

revision = '0009_ad_annotations'
down_revision = '0008_recent_collection'
branch_labels = None
depends_on = None


def upgrade():
    # 0010_partner_api can create this legacy table first on a clean install.
    op.execute("""
        CREATE TABLE IF NOT EXISTS l2_ad_annotations (
            ad_archive_id TEXT PRIMARY KEY REFERENCES ads(ad_archive_id) ON DELETE CASCADE,
            favorite BOOLEAN NOT NULL DEFAULT FALSE,
            tags TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade():
    op.execute("DROP TABLE l2_ad_annotations")

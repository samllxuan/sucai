"""Store source-provided drama genres with each landing-page snapshot."""
from alembic import op

revision = '0011_drama_metadata'
down_revision = ('0009_ad_annotations', '0010_partner_api')
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE page_snapshots
        ADD COLUMN genres TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
        ADD COLUMN genres_source TEXT
    """)


def downgrade():
    op.drop_column('page_snapshots', 'genres_source')
    op.drop_column('page_snapshots', 'genres')

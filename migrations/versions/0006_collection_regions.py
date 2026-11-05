"""Persist regional search selection independently of ISO-2 country settings."""
from alembic import op

revision = '0006_collection_regions'
down_revision = '0005_ad_public_metadata'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE app_settings ADD COLUMN collect_region TEXT CHECK (collect_region IN ('EUROPE'))")


def downgrade():
    op.drop_column('app_settings', 'collect_region')

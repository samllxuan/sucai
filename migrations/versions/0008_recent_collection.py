"""Persist explicit collection date window and source ordering."""
from alembic import op

revision = '0008_recent_collection'
down_revision = '0007_collected_app_keywords'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE app_settings
      ADD COLUMN collect_launch_days INTEGER NOT NULL DEFAULT 7 CHECK (collect_launch_days IN (0,7,14,30)),
      ADD COLUMN collect_sort TEXT NOT NULL DEFAULT 'newest' CHECK (collect_sort IN ('newest','default'))""")


def downgrade():
    op.drop_column('app_settings', 'collect_sort')
    op.drop_column('app_settings', 'collect_launch_days')

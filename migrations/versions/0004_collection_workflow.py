"""Public browser collection controls and honest sampling progress."""
from alembic import op

revision = '0004_collection_workflow'
down_revision = '0003_zh_language'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE app_settings
      ADD COLUMN collect_mode TEXT NOT NULL DEFAULT 'pages' CHECK (collect_mode IN ('pages','keyword')),
      ADD COLUMN collect_keyword TEXT NOT NULL DEFAULT '',
      ADD COLUMN collect_ad_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (collect_ad_status IN ('ACTIVE','INACTIVE','ALL')),
      ADD COLUMN self_hosted_max_ads INTEGER NOT NULL DEFAULT 30 CHECK (self_hosted_max_ads BETWEEN 1 AND 200)""")
    op.execute("""ALTER TABLE collection_runs
      ADD COLUMN partial_pages INTEGER NOT NULL DEFAULT 0,
      ADD COLUMN collection_method TEXT,
      ADD COLUMN scope TEXT,
      ADD COLUMN media_total INTEGER NOT NULL DEFAULT 0,
      ADD COLUMN media_saved INTEGER NOT NULL DEFAULT 0""")


def downgrade():
    for col in ('partial_pages', 'collection_method', 'scope', 'media_total', 'media_saved'):
        op.drop_column('collection_runs', col)
    for col in ('collect_mode', 'collect_keyword', 'collect_ad_status', 'self_hosted_max_ads'):
        op.drop_column('app_settings', col)

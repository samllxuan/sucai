"""Distinguish public coverage evidence from country-filter search observations."""
from alembic import op

revision = '0005_ad_public_metadata'
down_revision = '0004_collection_workflow'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE ads
      ADD COLUMN observed_countries TEXT[] NOT NULL DEFAULT '{}',
      ADD COLUMN reported_countries TEXT[] NOT NULL DEFAULT '{}',
      ADD COLUMN coverage_countries TEXT[] NOT NULL DEFAULT '{}',
      ADD COLUMN coverage_status TEXT NOT NULL DEFAULT 'not_checked'
        CHECK (coverage_status IN ('not_checked','available','empty','failed')),
      ADD COLUMN coverage_checked_at TIMESTAMPTZ,
      ADD COLUMN coverage_attempted_at TIMESTAMPTZ,
      ADD COLUMN coverage_source TEXT,
      ADD COLUMN coverage_error TEXT,
      ADD COLUMN coverage_data JSONB""")


def downgrade():
    for col in ('observed_countries','reported_countries','coverage_countries','coverage_status',
                'coverage_checked_at','coverage_attempted_at','coverage_source','coverage_error','coverage_data'):
        op.drop_column('ads', col)

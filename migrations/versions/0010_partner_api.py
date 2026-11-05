"""Partner API keys and durable search jobs."""
from alembic import op

revision = '0010_partner_api'
down_revision = '0009_keyword_matching'
branch_labels = None
depends_on = None


def upgrade():
    # Earlier L2 deployments created these tables outside Alembic. Preserve them,
    # while making clean installs from this repository work as well.
    op.execute('''CREATE TABLE IF NOT EXISTS l2_chart_snapshots (
      source TEXT PRIMARY KEY, payload JSONB NOT NULL, fetched_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE IF NOT EXISTS l2_ad_annotations (
      ad_archive_id TEXT PRIMARY KEY, favorite BOOLEAN NOT NULL DEFAULT false,
      tags TEXT NOT NULL, notes TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE partner_api_keys (
      key_hash TEXT PRIMARY KEY, label TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      revoked_at TIMESTAMPTZ)''')
    op.execute('''CREATE TABLE partner_search_jobs (
      id UUID PRIMARY KEY, key_hash TEXT NOT NULL REFERENCES partner_api_keys(key_hash),
      title TEXT NOT NULL, country TEXT NOT NULL DEFAULT 'US', status TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ,
      finished_at TIMESTAMPTZ, run_id INTEGER REFERENCES collection_runs(id),
      partial BOOLEAN NOT NULL DEFAULT false, error TEXT,
      CHECK (status IN ('queued','running','completed','no_results','failed')),
      CHECK (country ~ '^[A-Z]{2}$'))''')
    op.execute('CREATE INDEX partner_search_queue_idx ON partner_search_jobs(status,created_at)')


def downgrade():
    op.drop_table('partner_search_jobs')
    op.drop_table('partner_api_keys')

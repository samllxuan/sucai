"""Keep the exact keyword of each collection for reliable App search grouping."""
from alembic import op

revision = '0007_collected_app_keywords'
down_revision = '0006_collection_regions'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE collection_runs ADD COLUMN search_keyword TEXT')
    # Older browser jobs retained the full query at the end of their scope.
    op.execute("""UPDATE collection_runs SET search_keyword=
        substring(scope FROM position('; query=' IN scope)+8)
        WHERE scope LIKE 'keyword; %' AND position('; query=' IN scope)>0""")
    op.execute('CREATE INDEX ix_collection_runs_keyword ON collection_runs (lower(btrim(search_keyword))) WHERE search_keyword IS NOT NULL')


def downgrade():
    op.drop_index('ix_collection_runs_keyword', table_name='collection_runs')
    op.drop_column('collection_runs', 'search_keyword')

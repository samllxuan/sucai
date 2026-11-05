"""Allow Simplified Chinese as an instance interface language.

Revision ID: 0003_zh_language
Revises: 0002_apify_actor_runs
"""
from alembic import op

revision = '0003_zh_language'
down_revision = '0002_apify_actor_runs'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('app_settings_language_check', 'app_settings', type_='check')
    op.create_check_constraint('app_settings_language_check', 'app_settings',
                               "language IN ('en', 'es', 'pt', 'zh')")


def downgrade():
    op.execute("UPDATE app_settings SET language = 'en' WHERE language = 'zh'")
    op.drop_constraint('app_settings_language_check', 'app_settings', type_='check')
    op.create_check_constraint('app_settings_language_check', 'app_settings',
                               "language IN ('en', 'es', 'pt')")

"""Add TikTok as a collection platform without changing Meta identifiers."""

from alembic import op


revision = "0012_tiktok_platform"
down_revision = "0011_drama_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ads
          ADD COLUMN platform TEXT NOT NULL DEFAULT 'meta'
            CHECK (platform IN ('meta', 'tiktok')),
          ADD COLUMN external_ad_id TEXT,
          ADD COLUMN advertiser_name TEXT,
          ADD COLUMN advertiser_id TEXT,
          ADD COLUMN registry_location TEXT,
          ADD COLUMN sponsor TEXT;

        UPDATE ads
           SET external_ad_id = ad_archive_id
         WHERE external_ad_id IS NULL;

        ALTER TABLE pages
          ADD COLUMN platform TEXT NOT NULL DEFAULT 'meta'
            CHECK (platform IN ('meta', 'tiktok'));

        ALTER TABLE app_settings
          ADD COLUMN collect_platform TEXT NOT NULL DEFAULT 'meta'
            CHECK (collect_platform IN ('meta', 'tiktok'));

        CREATE INDEX idx_ads_platform ON ads(platform);
        CREATE INDEX idx_pages_platform ON pages(platform);
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM ads WHERE platform = 'tiktok';
        DELETE FROM pages WHERE platform = 'tiktok';

        DROP INDEX IF EXISTS idx_pages_platform;
        DROP INDEX IF EXISTS idx_ads_platform;

        ALTER TABLE app_settings DROP COLUMN IF EXISTS collect_platform;
        ALTER TABLE pages DROP COLUMN IF EXISTS platform;
        ALTER TABLE ads
          DROP COLUMN IF EXISTS sponsor,
          DROP COLUMN IF EXISTS registry_location,
          DROP COLUMN IF EXISTS advertiser_id,
          DROP COLUMN IF EXISTS advertiser_name,
          DROP COLUMN IF EXISTS external_ad_id,
          DROP COLUMN IF EXISTS platform;
    """)

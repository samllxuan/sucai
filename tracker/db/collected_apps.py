"""Search-target summaries based on real run appearances, never inferred App ownership."""
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .base import connect


def list_collected_apps():
    with connect() as conn:
        rows = conn.execute("""
          SELECT lower(btrim(r.search_keyword)) AS key,
            (array_agg(btrim(r.search_keyword) ORDER BY r.started_at DESC,r.id DESC))[1] AS name,
            count(DISTINCT r.id) AS runs,
            count(DISTINCT a.id) AS ad_count,
            count(DISTINCT COALESCE(a.collation_id,'ad:'||a.ad_archive_id)) AS card_count,
            count(DISTINCT a.id) FILTER (WHERE a.is_active IS TRUE) AS active_count,
            count(DISTINCT a.page_ref_id) AS page_count,
            max(x.seen_at) AS last_collected_at,
            max(r.started_at) AS last_run_at
          FROM collection_runs r
          LEFT JOIN ad_appearances x ON x.collection_run_id=r.id
          LEFT JOIN ads a ON a.id=x.ad_id AND EXISTS (SELECT 1 FROM creatives cr WHERE cr.ad_id=a.id)
          WHERE r.search_keyword IS NOT NULL AND btrim(r.search_keyword)<>''
          GROUP BY lower(btrim(r.search_keyword))
          ORDER BY max(r.started_at) DESC,lower(btrim(r.search_keyword))
        """).fetchall()
    for row in rows:
        row['url'] = '/?' + urlencode({'collected_app': row['key'], 'status': 'all', 'sort': 'launch'}) + '#library'
        row['collect_url'] = '/collect?' + urlencode({'keyword': row['name']})
        when = row['last_collected_at']
        row['last_collected_label'] = when.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M') if when else '尚无素材'
    return rows

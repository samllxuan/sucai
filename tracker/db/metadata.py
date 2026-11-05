"""Small public metadata payloads; full demographic tables stay in the database."""
from functools import lru_cache

from babel import Locale
from .base import _date_only, connect


@lru_cache(maxsize=1)
def _names():
    return Locale.parse('zh').territories


def country_names(codes):
    return [_names().get(code, code) for code in codes or []]


def coverage_details(ad_archive_id):
    """Fetch demographic rows only for the requested ad, preserving undisclosed values."""
    with connect() as conn:
        row = conn.execute('SELECT coverage_data FROM ads WHERE ad_archive_id=%s', (ad_archive_id,)).fetchone()
    regions = (row or {}).get('coverage_data') or {}
    result = []
    for region in regions.get('regions', []):
        rows = []
        for item in region.get('rows') or []:
            code = item.get('country') or ''
            rows.append({**item, 'country_name': _names().get(code, code)})
        result.append({**region, 'rows': rows})
    return result


def metadata_for_ads(ad_ids):
    if not ad_ids:
        return {}
    with connect() as conn:
        rows = conn.execute('''SELECT ad_archive_id,delivery_start_time,delivery_stop_time,
             first_seen_at,last_seen_at,observed_countries,reported_countries,coverage_countries,
             coverage_status,coverage_checked_at,coverage_attempted_at,coverage_source,coverage_error,
             (SELECT jsonb_agg(jsonb_build_object('region',v->>'region','reach',v->'reach'))
              FROM jsonb_array_elements(COALESCE(coverage_data->'regions','[]'::jsonb)) AS v)
              AS reach_summary
             FROM ads WHERE ad_archive_id=ANY(%s)''', (list(ad_ids),)).fetchall()
    result = {}
    for r in rows:
        item = {
            'delivery_start': _date_only(r['delivery_start_time']),
            'delivery_stop': _date_only(r['delivery_stop_time']),
            'first_seen': _date_only(r['first_seen_at']),
            'last_seen': _date_only(r['last_seen_at']),
            'coverage_countries': r['coverage_countries'],
            'coverage_country_names': country_names(r['coverage_countries']),
            'observed_countries': r['observed_countries'],
            'observed_country_names': country_names(r['observed_countries']),
            'reported_countries': r['reported_countries'],
            'reported_country_names': country_names(r['reported_countries']),
            'coverage_status': r['coverage_status'],
            'coverage_checked_at': r['coverage_checked_at'].isoformat() if r['coverage_checked_at'] else None,
            'coverage_attempted_at': r['coverage_attempted_at'].isoformat() if r['coverage_attempted_at'] else None,
            'coverage_source': r['coverage_source'],
            'coverage_error': r['coverage_error'],
            'reach_summary': r['reach_summary'] or [],
        }
        result[r['ad_archive_id']] = item
    return result

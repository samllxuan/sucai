"""Read Meta's public EU/UK coverage disclosures; never infer coverage from a search filter."""
import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone

from psycopg.types.json import Jsonb
from .. import db

DOC_ID = os.environ.get('META_AD_DETAILS_DOC_ID', '25068828942793558')
QUERY_NAME = 'AdLibraryV3AdDetailsQuery'


def number(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d+(?:,\d{3})*', value.strip()):
        return int(value.strip().replace(',', ''))
    return None


def country_codes(values):
    result = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get('country_code') or value.get('country') or value.get('code') or value.get('key')
        if isinstance(value, str) and re.fullmatch('[A-Za-z]{2}', value):
            result.add('GB' if value.upper() == 'UK' else value.upper())
    return sorted(result)


def parse_details(payload, page_id):
    if payload.get('errors'):
        raise ValueError('Meta 详情接口返回错误，已有覆盖数据保留')
    node = ((payload.get('data') or {}).get('ad_library_main') or {}).get('ad_details')
    if not isinstance(node, dict):
        raise ValueError('Meta 未返回有效广告详情，已有覆盖数据保留')
    returned_page = ((node.get('advertiser') or {}).get('page') or {}).get('id')
    if returned_page is not None and str(returned_page) != str(page_id):
        raise ValueError('详情主页与请求不一致，未更新覆盖数据')
    transparency = node.get('transparency_by_location')
    if not isinstance(transparency, dict) or not any(k in transparency for k in ('eu_transparency','uk_transparency')):
        raise ValueError('Meta 详情缺少地区披露字段，已有数据保留')
    regions = []
    for region, field, total in (('EU','eu_transparency','eu_total_reach'),('GB','uk_transparency','total_reach')):
        block = transparency.get(field)
        if not isinstance(block, dict):
            continue
        rows = []
        for country in block.get('age_country_gender_reach_breakdown') or []:
            codes = country_codes([country.get('country')])
            if not codes:
                continue
            for group in country.get('age_gender_breakdowns') or []:
                rows.append({'country': codes[0], 'age': str(group.get('age_range') or '未注明'),
                             **{g:number(group.get(g)) for g in ('male','female','unknown')}})
        regions.append({'region':region,'reach':number(block.get(total)),'rows':rows})
    return {'regions':regions}


def reached_countries(parsed):
    # Target locations and a country selected in the search form are not reach evidence.
    return sorted({row['country'] for region in parsed.get('regions',[]) for row in region.get('rows',[])
                   if any(number(row.get(g)) is not None and number(row.get(g)) > 0
                          for g in ('male','female','unknown'))})


async def fetch_details(client, ad_id, page_id):
    if not str(ad_id).isdigit() or not str(page_id).isdigit():
        raise ValueError('缺少有效广告或主页 ID')
    variables = {'adArchiveID':str(ad_id),'pageID':str(page_id),'country':'GB',
                 'sessionID':str(uuid.uuid4()),'source':None,'isAdNonPolitical':True,'isAdNotAAAEligible':False}
    payload = client._build_graphql_payload(DOC_ID,variables,QUERY_NAME)
    headers = dict(client._fingerprint.get_graphql_headers())
    headers.update({'x-fb-friendly-name':QUERY_NAME,'x-fb-lsd':client._tokens.get('lsd',''),
                    'referer':f'https://www.facebook.com/ads/library/?id={ad_id}'})
    response = await client._make_request('POST',client.GRAPHQL_URL,data=payload,headers=headers)
    if response.status_code != 200:
        raise ValueError(f'Meta 详情请求失败（HTTP {response.status_code}），已有数据保留')
    return parse_details(json.loads(response.text.removeprefix('for (;;);')),page_id)


def save_result(conn, ad_id, parsed=None, error='', observed_at=None, source='Meta 公开详情'):
    observed_at = observed_at or datetime.now(timezone.utc)
    if error:
        conn.execute('''UPDATE ads SET coverage_status='failed',coverage_attempted_at=%s,
                     coverage_error=%s WHERE id=%s''',(observed_at,error[:350],ad_id))
    else:
        available = any(r.get('reach') is not None or r.get('rows') for r in parsed.get('regions',[]))
        # Empty/missing later responses do not erase usable historical evidence.
        conn.execute('''UPDATE ads SET coverage_status=%s,coverage_attempted_at=%s,coverage_error=NULL,
                     coverage_countries=CASE WHEN %s THEN %s::text[] ELSE coverage_countries END,
                     coverage_data=CASE WHEN %s THEN %s ELSE coverage_data END,
                     coverage_checked_at=CASE WHEN %s THEN %s ELSE coverage_checked_at END,
                     coverage_source=CASE WHEN %s THEN %s ELSE coverage_source END WHERE id=%s''',
                     ('available' if available else 'empty',observed_at,
                      available,reached_countries(parsed),available,Jsonb(parsed),available,observed_at,available,source,ad_id))
    conn.commit()


async def refresh_ads(rows, on_progress=None):
    if not rows:
        return {'checked':0,'failed':0}
    from meta_ads_collector.async_client import AsyncMetaAdsClient
    checked, failed = 0, 0
    try:
        async with AsyncMetaAdsClient(timeout=12,max_retries=1) as client:
            if not await asyncio.wait_for(client.initialize(),35):
                raise ValueError('Meta 详情会话初始化失败')
            consecutive_errors = 0
            for row in rows:
                try:
                    parsed = await asyncio.wait_for(fetch_details(client,row['ad_archive_id'],row['page_id']),35)
                    with db.connect() as conn:
                        save_result(conn,row['id'],parsed)
                    consecutive_errors = 0
                except Exception as exc:
                    failed += 1; consecutive_errors += 1
                    message = str(exc) if isinstance(exc,ValueError) and str(exc).startswith(('Meta ','详情','缺少')) else '公开覆盖读取失败，请稍后重试；已有数据保留'
                    with db.connect() as conn:
                        save_result(conn,row['id'],error=message)
                checked += 1
                if on_progress:
                    on_progress(checked,len(rows))
                if consecutive_errors >= 3:
                    break  # avoid repeatedly hitting a limited/changed endpoint
                if checked < len(rows):
                    await asyncio.sleep(0.4)
    except Exception:
        # Never persist request tokens, cookies or raw request/exception bodies.
        for row in rows[checked:checked+1]:
            with db.connect() as conn:
                save_result(conn,row['id'],error='公开覆盖会话不可用，请稍后重试；已有数据保留')
            failed += 1
    return {'checked':checked,'failed':failed}


def refresh_pending(on_progress=None):
    with db.connect() as conn:
        rows = conn.execute('''SELECT id,ad_archive_id,page_id FROM ads
             WHERE coverage_attempted_at IS NULL OR coverage_attempted_at<now()-interval '24 hours'
             ORDER BY coverage_attempted_at NULLS FIRST,last_seen_at DESC,id DESC LIMIT 100''').fetchall()
    return asyncio.run(refresh_ads(rows,on_progress))

"""L2-hosted SocialPeta public chart snapshot; source data remains attributed."""
import json
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg.types.json import Jsonb
from ... import db
from ..deps import _authed, templates

router = APIRouter()
SOURCE = 'https://socialpeta.com/en/drama-charts'
MARKER = '"chart":"drama","locale":"en","data":'


def parse_chart(html):
    for part in html.split('<script>self.__next_f.push([1,')[1:]:
        try:
            chunk, _ = json.JSONDecoder().raw_decode(part)
        except ValueError:
            continue
        pos = chunk.find(MARKER)
        if pos < 0:
            continue
        payload, _ = json.JSONDecoder().raw_decode(chunk[pos + len(MARKER):])
        rows = payload.get('rows') or []
        if not re.fullmatch(r'\d{8}', str(payload.get('date', ''))) or payload.get('period') != 'daily':
            raise ValueError('来源日期或周期不符合预期')
        if len(rows) != 100 or [r.get('rank') for r in rows] != list(range(1, 101)):
            raise ValueError('来源榜单不完整')
        clean = []
        for row in rows:
            name = str(row.get('name') or '').strip()
            app = str((row.get('primaryApp') or {}).get('appName') or '').strip()
            if not name or len(name) > 500 or len(app) > 500:
                raise ValueError('来源剧名或应用名称异常')
            clean.append({
                'rank': row['rank'], 'name': name, 'app': app,
                'language': str(row.get('languageCode') or ''),
                'creatives': row.get('creativeCount'),
                'new_creatives': row.get('newCreativeCount'),
                'estimated_impressions': row.get('estimatedImpressions'),
                'active_days': row.get('creativeDays'),
                'countries': [str(x.get('code') or '') for x in (row.get('topCountries') or [])[:3]],
            })
        return {'date': payload['date'], 'period': 'daily', 'language': payload.get('language', 'all'), 'rows': clean}
    raise ValueError('来源页面未返回榜单数据')


def fetch_chart():
    req = urllib.request.Request(SOURCE, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=35) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError('来源页面过大')
    return parse_chart(raw.decode('utf-8'))


@router.get('/charts/socialpeta', response_class=HTMLResponse)
def chart(request: Request):
    if not _authed(request):
        return RedirectResponse('/login?next=/charts/socialpeta', status_code=302)
    with db.connect() as conn:
        record = conn.execute('SELECT payload, fetched_at FROM l2_chart_snapshots WHERE source=%s', ('socialpeta_daily',)).fetchone()
        data = record['payload'] if record else {'rows': []}
        titles = [row['name'].lower() for row in data['rows']]
        rows = conn.execute('''SELECT lower(btrim(cr.title)) AS title, count(DISTINCT a.id) AS n,
          (array_agg(DISTINCT a.ad_archive_id))[1:3] AS ids
          FROM ads a JOIN creatives cr ON cr.ad_id=a.id
          WHERE lower(btrim(cr.title))=ANY(%s) GROUP BY lower(btrim(cr.title))''', (titles,)).fetchall() if titles else []
    matches = {r['title']: r for r in rows}
    q = request.query_params.get('q', '').strip()[:100]
    filtered = [r for r in data['rows'] if not q or q.casefold() in (r['name']+' '+r['app']).casefold()]
    date = data.get('date', '')
    date_label = f'{date[:4]}-{date[4:6]}-{date[6:]}' if len(date)==8 else '暂无数据'
    fetched = record['fetched_at'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if record else ''
    return templates.TemplateResponse(request, 'socialpeta_chart.html', {
        'rows': filtered, 'total': len(data['rows']), 'date_label': date_label,
        'fetched': fetched, 'matches': matches, 'q': q,
    }, headers={'Cache-Control':'no-store'})


@router.post('/charts/socialpeta/refresh')
def refresh(request: Request):
    if not _authed(request):
        return RedirectResponse('/login?next=/charts/socialpeta',status_code=302)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).netloc != request.headers.get('host'):
        return HTMLResponse('来源不正确',status_code=403)
    try:
        data=fetch_chart()
        with db.connect() as conn:
            conn.execute('''INSERT INTO l2_chart_snapshots (source,payload,fetched_at)
              VALUES (%s,%s,now()) ON CONFLICT (source) DO UPDATE
              SET payload=EXCLUDED.payload,fetched_at=EXCLUDED.fetched_at''',
              ('socialpeta_daily',Jsonb(data)))
    except Exception:
        return RedirectResponse('/charts/socialpeta?refresh=failed',status_code=303)
    return RedirectResponse('/charts/socialpeta?refresh=ok',status_code=303)

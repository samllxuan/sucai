"""A single, visible entry point for configuring and starting real collections."""
import json
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse

from ... import db, i18n
from ...collection_scopes import REGIONS, resolve_scope
from ...collection_window import WINDOWS, SORTS
from ..deps import _authed, request_lang, templates
from .admin import public_settings

router = APIRouter()


def parse_page_id(value: str) -> str:
    value = value.strip()
    if value.isascii() and value.isdigit():
        return value
    parsed = urlparse(value)
    if parsed.scheme == 'https' and (parsed.hostname or '').lower() in (
            'facebook.com', 'www.facebook.com', 'm.facebook.com'):
        params = parse_qs(parsed.query)
        candidate = (params.get('view_all_page_id') or params.get('id') or [''])[0]
        if candidate.isascii() and candidate.isdigit():
            return candidate
        tail = parsed.path.rstrip('/').split('/')[-1]
        if tail.isascii() and tail.isdigit():
            return tail
    raise ValueError('请输入数字 Page ID，或包含 view_all_page_id 的 Meta 广告库主页链接')


@router.get('/collect')
def collect_home(request: Request):
    if not _authed(request):
        return RedirectResponse('/login', status_code=302)
    settings = public_settings(db.get_app_settings())
    keyword = request.query_params.get('keyword','').strip()[:200]
    if keyword:
        settings.update(collect_mode='keyword',collect_keyword=keyword)
    return templates.TemplateResponse(request, 'collect.html', {
        'settings': settings,
        'companies': db.list_companies_with_pages(),
        'countries': i18n.countries(request_lang(request)),
        'regions': REGIONS,
        'run_status_json': json.dumps(jsonable_encoder(db.get_run_status())).replace('</', '<\\/'),
    })


def _error(message, status=400):
    return JSONResponse({'error': message}, status_code=status, headers={'Cache-Control': 'no-store'})


@router.post('/collect/start')
def collect_start(request: Request, mode: str = Form('keyword'), keyword: str = Form(''),
                  page_ids: list[int] = Form([]), new_page: str = Form(''),
                  page_name: str = Form(''), country: str = Form('US'),
                  ad_status: str = Form('ACTIVE'), max_ads: int = Form(30),
                  interval_hours: int = Form(24), launch_days: int = Form(7),
                  collection_sort: str = Form('newest'), match_mode: str = Form('exact')):
    if not _authed(request):
        return _error('unauthorized', 401)
    country, keyword, new_page = country.strip().upper(), keyword.strip(), new_page.strip()
    if mode not in ('pages', 'keyword') or ad_status not in ('ACTIVE', 'INACTIVE', 'ALL') or match_mode not in ('exact', 'broad'):
        return _error('采集方式或广告状态无效')
    if launch_days not in WINDOWS or collection_sort not in SORTS:
        return _error('请选择有效的上线时间范围和采集排序')
    try:
        region, country = resolve_scope(country, dict(i18n.countries(i18n.DEFAULT_LANGUAGE)))
    except ValueError as exc:
        return _error(str(exc))
    if not 1 <= max_ads <= 200 or not 1 <= interval_hours <= 168:
        return _error('每次搜索上限为 1–200 条，自动采集间隔为 1–168 小时')
    if mode == 'keyword' and not 1 <= len(keyword) <= 200:
        return _error('请输入 App 名称或关键词，最多 200 个字符')
    if mode == 'pages' and not page_ids and not new_page:
        return _error('请至少选择一个主页，或填写新主页 ID / 广告库主页链接')
    if len(page_name) > 200 or len(new_page) > 2000:
        return _error('主页名称或链接过长')
    new_id = None
    if mode == 'pages' and new_page:
        try:
            new_id = parse_page_id(new_page)
        except ValueError as exc:
            return _error(str(exc))
    with db.connect() as conn:
        settings = conn.execute('SELECT * FROM app_settings WHERE id=1 FOR UPDATE').fetchone()
        running = conn.execute('''SELECT 1 FROM collection_runs WHERE finished_at IS NULL
            AND COALESCE(heartbeat_at,started_at)>=now()-interval '15 minutes' LIMIT 1''').fetchone()
        recent_request = conn.execute('''SELECT 1 FROM app_settings s WHERE id=1
            AND collect_now_requested_at>now()-interval '60 seconds'
            AND NOT EXISTS (SELECT 1 FROM collection_runs r WHERE r.started_at>=s.collect_now_requested_at)''').fetchone()
        if settings['collect_now'] or running or recent_request:
            return _error('已有采集任务在排队或运行，请等待完成后再开始', 409)
        if mode == 'pages':
            existing = conn.execute('SELECT id FROM pages WHERE id=ANY(%s)', (page_ids,)).fetchall()
            if len(existing) != len(set(page_ids)):
                return _error('部分主页不存在，请刷新后重新选择')
            if new_id:
                row = conn.execute('SELECT id FROM pages WHERE page_id=%s', (new_id,)).fetchone()
                if not row:
                    company = conn.execute('INSERT INTO companies (name) VALUES (%s) RETURNING id',
                                           (page_name.strip() or f'Meta {new_id}',)).fetchone()
                    row = conn.execute('''INSERT INTO pages (company_id,page_id,page_name,collect_country)
                         VALUES (%s,%s,%s,%s) RETURNING id''',
                         (company['id'],new_id,page_name.strip() or None,country)).fetchone()
                page_ids = [*page_ids, row['id']]
            conn.execute('UPDATE pages SET is_tracked=(id=ANY(%s))', (page_ids,))
            if not region:
                conn.execute('UPDATE pages SET collect_country=%s WHERE id=ANY(%s)', (country,page_ids))
        conn.execute('''UPDATE app_settings SET collect_source='self_hosted', collect_mode=%s,
          collect_keyword=%s, collect_country=%s, collect_region=%s, collect_ad_status=%s, self_hosted_max_ads=%s,
          collect_interval_hours=%s, collect_launch_days=%s, collect_sort=%s, collect_match_mode=%s,
          collect_now=TRUE,collect_now_requested_at=now(),updated_at=now()
          WHERE id=1''', (mode,keyword,country,region,ad_status,max_ads,interval_hours,launch_days,collection_sort,match_mode))
    return JSONResponse({**jsonable_encoder(db.get_run_status()), 'accepted': True},
                        headers={'Cache-Control': 'no-store'})

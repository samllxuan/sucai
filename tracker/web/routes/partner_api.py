"""Versioned partner API, independent of website login and collection settings."""
import base64
import binascii
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ... import db, i18n
from ...db.base import resolve_media_url
from ...db.metadata import coverage_details

router = APIRouter(prefix='/api/v1', tags=['partner'])


def authenticate(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'API key required', headers={'WWW-Authenticate':'Bearer'})
    token = authorization[7:].strip()
    if not token or len(token) > 256:
        raise HTTPException(401, 'Invalid API key')
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    with db.connect() as c:
        key = c.execute('SELECT key_hash FROM partner_api_keys WHERE key_hash=%s AND revoked_at IS NULL', (digest,)).fetchone()
    if not key:
        raise HTTPException(401, 'Invalid API key', headers={'WWW-Authenticate':'Bearer'})
    return digest


class SearchBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    country: str = Field(default='US', min_length=2, max_length=2)


def existing(title, page=0):
    return db.get_library_page(db.LibraryFilters(q=title, status='all'), sort='launch', page=page)


def job_results(run_id, page=0):
    """Return this collection's actual observations, even if Meta varies punctuation."""
    size=20
    if not run_id:
        return {'cards':[], 'total':0, 'ad_count':0, 'page':0, 'pages':1, 'page_size':size}
    with db.connect() as c:
        n=c.execute('SELECT count(DISTINCT ad_id) AS n FROM ad_appearances WHERE collection_run_id=%s',(run_id,)).fetchone()['n']
        pages=max(1,-(-n//size)); page=min(page,pages-1)
        rows=c.execute("""SELECT DISTINCT a.id,a.ad_archive_id,a.page_name,a.is_active,a.body,
           a.delivery_start_time,cr.title FROM ad_appearances x JOIN ads a ON a.id=x.ad_id
           LEFT JOIN LATERAL (SELECT title FROM creatives WHERE ad_id=a.id
                ORDER BY creative_index NULLS LAST,id LIMIT 1) cr ON true
           WHERE x.collection_run_id=%s ORDER BY a.id LIMIT %s OFFSET %s""",(run_id,size,page*size)).fetchall()
    cards=[{'id':r['ad_archive_id'],'page':r['page_name'],'active':r['is_active'],
            'title':r['title'] or '', 'body':r['body'] or '', 'delivery_start':r['delivery_start_time']}
           for r in rows]
    return {'cards':cards,'total':n,'ad_count':n,'page':page,'pages':pages,'page_size':size}


@router.post('/searches', status_code=202)
def search(body: SearchBody, request: Request, key=Depends(authenticate)):
    title = ' '.join(body.title.split())
    country = body.country.upper()
    if not title or not re.search(r'\w', title, flags=re.UNICODE):
        raise HTTPException(422, 'title must contain letters or numbers')
    if country not in dict(i18n.countries(i18n.DEFAULT_LANGUAGE)):
        raise HTTPException(422, 'Invalid country')
    data = existing(title)
    if data['ad_count']:
        return {'status':'completed', 'source':'l2_library', 'title':title,
                'country':country, 'results':data, 'note':'已有素材按文字匹配；国家不限制已有数据。'}
    with db.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)', (731960021,))
        previous = c.execute("""SELECT id FROM partner_search_jobs WHERE key_hash=%s
           AND lower(title)=lower(%s) AND country=%s AND status IN ('queued','running')
           ORDER BY created_at DESC LIMIT 1""", (key,title,country)).fetchone()
        if previous:
            jid = previous['id']
        else:
            active = c.execute("SELECT count(*) AS n FROM partner_search_jobs WHERE status IN ('queued','running')").fetchone()['n']
            if active >= 20:
                raise HTTPException(429, 'Collection queue is full; retry later')
            jid = uuid.uuid4()
            c.execute("INSERT INTO partner_search_jobs(id,key_hash,title,country,status) VALUES(%s,%s,%s,%s,'queued')", (jid,key,title,country))
    return {'status':'queued', 'job_id':str(jid), 'status_url':str(request.base_url).rstrip('/')+'/api/v1/searches/'+str(jid)}


@router.get('/searches/{job_id}')
def search_status(job_id: uuid.UUID, page: int = Query(0, ge=0, le=10000), key=Depends(authenticate)):
    with db.connect() as c:
        row = c.execute("SELECT id,title,country,status,created_at,started_at,finished_at,run_id,partial,error FROM partner_search_jobs WHERE id=%s AND key_hash=%s", (job_id,key)).fetchone()
    if not row:
        raise HTTPException(404, 'Search job not found')
    row['id'] = str(row['id'])
    if row['status'] in ('completed','no_results'):
        row['results'] = job_results(row['run_id'],page)
        row['note'] = '结果为本次采集到的文字匹配记录；未发现不代表外部平台没有投放。'
    return row


def encode_cursor(timestamp, row_id, until):
    raw = json.dumps({'t':timestamp.isoformat(),'i':row_id,'u':until.isoformat()},separators=(',',':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode_cursor(value):
    try:
        item = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value)%4)))
        t = datetime.fromisoformat(item['t']); until = datetime.fromisoformat(item['u']); row_id = int(item['i'])
        if t.tzinfo is None or until.tzinfo is None or row_id < 0 or t > until:
            raise ValueError()
        return t,row_id,until
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(400, 'Invalid cursor')


@router.get('/ads')
def list_ads(request: Request, limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None,max_length=512),
             updated_since: datetime | None = None, key=Depends(authenticate)):
    if cursor and updated_since:
        raise HTTPException(400, 'Use cursor or updated_since, not both')
    if updated_since and updated_since.tzinfo is None:
        raise HTTPException(400, 'updated_since must include timezone')
    if cursor:
        after, after_id, until = decode_cursor(cursor)
    else:
        after, after_id, until = updated_since or datetime(1970,1,1,tzinfo=timezone.utc), 0, datetime.now(timezone.utc)
    with db.connect() as c:
        rows = c.execute("""SELECT a.id,a.ad_archive_id,a.page_id,a.page_name,a.is_active,
           a.delivery_start_time,a.delivery_stop_time,a.first_seen_at,a.last_seen_at,
           a.observed_countries,a.coverage_countries,a.coverage_checked_at,a.coverage_source,
           a.publisher_platforms,a.body,cr.title,cr.image_url,cr.video_url,cr.thumbnail_url,
           cr.media_image_key,cr.media_video_key,cr.media_thumb_key
           FROM ads a LEFT JOIN LATERAL (SELECT * FROM creatives WHERE ad_id=a.id
              ORDER BY creative_index NULLS LAST,id LIMIT 1) cr ON true
           WHERE (a.last_seen_at,a.id)>(%s,%s) AND a.last_seen_at<=%s
           ORDER BY a.last_seen_at,a.id LIMIT %s""",(after,after_id,until,limit+1)).fetchall()
    more = len(rows)>limit; rows = rows[:limit]
    for row in rows:
        row['detail_url'] = str(request.base_url).rstrip('/')+'/api/v1/ads/'+row['ad_archive_id']
        for typ in ('image','video','thumb'):
            src = 'thumbnail_url' if typ=='thumb' else typ+'_url'
            row[src] = resolve_media_url(row[src],row.pop('media_'+typ+'_key'))
            if row[src] and row[src].startswith('/'):
                row[src] = str(request.base_url).rstrip('/')+row[src]
    next_cursor = encode_cursor(rows[-1]['last_seen_at'],rows[-1]['id'],until) if more and rows else None
    return {'items':rows,'next_cursor':next_cursor,'snapshot_until':until,
            'note':'按素材最后观测时间分页；覆盖详情单独更新时不会改变该时间。'}


@router.get('/ads/{ad_id}')
def ad(ad_id: str, key=Depends(authenticate)):
    if not ad_id.isdigit() or len(ad_id)>32:
        raise HTTPException(404, 'Ad not found')
    item = db.get_ad_detail(ad_id)
    if not item:
        raise HTTPException(404, 'Ad not found')
    item['coverage_regions'] = coverage_details(ad_id)
    return item


@router.get('/charts')
def charts(key=Depends(authenticate)):
    allowed = ('socialpeta_daily','shortdramadata_paid','shortdramadata_free','shortdramadata_hot')
    with db.connect() as c:
        rows = c.execute('SELECT source,payload,fetched_at FROM l2_chart_snapshots WHERE source=ANY(%s) ORDER BY source', (list(allowed),)).fetchall()
    return {'items':rows, 'note':'本站保存的来源榜单快照；请以各项的数据日期和读取时间判断时效。'}

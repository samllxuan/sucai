"""Short Drama Data's public read-only API, shown within L2."""
import json
import urllib.request
from datetime import timezone
from urllib.parse import urlparse
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg.types.json import Jsonb
from ... import db
from ..deps import _authed, templates

router=APIRouter()
SOURCES={
    'paid': 'https://www.shortdramadata.com/api/v1/rankings/paid?limit=200',
    'free': 'https://www.shortdramadata.com/api/v1/rankings/free?limit=200',
    'hot': 'https://www.shortdramadata.com/api/v1/hot?market=paid&lang=en&limit=50',
}

def get_dataset(kind):
    req=urllib.request.Request(SOURCES[kind],headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as r:
        body=r.read(2_000_001)
    if len(body)>2_000_000: raise ValueError('response too large')
    data=json.loads(body)
    items=data.get('items')
    if not isinstance(items,list) or not items or len(items)>200: raise ValueError('invalid items')
    if kind in ('paid','free'):
        date=str(data.get('rank_date') or '')
        if len(date)!=10 or any(not isinstance(i.get('rank'),int) or not i.get('app_name') for i in items): raise ValueError('invalid ranking')
        rows=[{'rank':i['rank'],'name':str(i['app_name'])[:500],'company':str(i.get('company') or '')[:500],
               'score':i.get('composite_score'),'coverage':i.get('coverage_countries'),
               'grossing':i.get('grossing_lead'),'free_lead':i.get('free_lead')} for i in items]
    else:
        if any(not i.get('title') for i in items):raise ValueError('invalid hot list')
        date=str(items[0].get('observation_date') or '')
        rows=[{'name':str(i['title'])[:500],'app':str(i.get('app_name') or '')[:500],
               'language':str(i.get('language_label') or i.get('language') or '')[:100],
               'date':str(i.get('observation_date') or '')[:20]} for i in items]
    return {'kind':kind,'date':date,'rows':rows,'total':data.get('total',len(rows))}

@router.get('/charts/shortdramadata',response_class=HTMLResponse)
def index(request:Request):
    if not _authed(request):return RedirectResponse('/login?next=/charts/shortdramadata',status_code=302)
    kind=request.query_params.get('kind','paid')
    if kind not in SOURCES:kind='paid'
    q=request.query_params.get('q','').strip()[:100]
    with db.connect() as c:
        record=c.execute('SELECT payload,fetched_at FROM l2_chart_snapshots WHERE source=%s',('shortdramadata_'+kind,)).fetchone()
    data=record['payload'] if record else {'rows':[],'total':0,'date':''}
    rows=[r for r in data['rows'] if not q or q.casefold() in (' '.join(str(r.get(k) or '') for k in ('name','company','app'))).casefold()]
    fetched=record['fetched_at'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if record else ''
    return templates.TemplateResponse(request,'shortdramadata_chart.html',{'kind':kind,'rows':rows,'q':q,'data':data,'fetched':fetched},headers={'Cache-Control':'no-store'})

@router.post('/charts/shortdramadata/refresh')
def refresh(request:Request,kind:str='paid'):
    if not _authed(request):return RedirectResponse('/login?next=/charts/shortdramadata',status_code=302)
    if kind not in SOURCES:return HTMLResponse('无效榜单',status_code=400)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).netloc!=request.headers.get('host'):return HTMLResponse('来源不正确',status_code=403)
    try:
        data=get_dataset(kind)
        with db.connect() as c:
            c.execute('''INSERT INTO l2_chart_snapshots(source,payload,fetched_at) VALUES(%s,%s,now())
              ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at''',('shortdramadata_'+kind,Jsonb(data)))
    except Exception:
        return RedirectResponse('/charts/shortdramadata?kind='+kind+'&refresh=failed',status_code=303)
    return RedirectResponse('/charts/shortdramadata?kind='+kind+'&refresh=ok',status_code=303)

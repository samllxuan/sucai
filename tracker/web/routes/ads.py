"""The Ads screen (the library, one page at a time) and the full-page ad detail."""

from __future__ import annotations

import html
import json
from urllib.parse import quote

from fastapi import APIRouter, Request, Form
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ... import db
from ...db.collected_apps import list_collected_apps
from ...i18n import N_, translate
from ..deps import _authed, _needs_setup, _setup_pending, _tier, request_lang, templates

router = APIRouter()


def selected_companies(request: Request) -> tuple[str, ...]:
    """The companies ticked in the Company filter (?company=A&company=B): names."""
    return tuple(dict.fromkeys(c.strip() for c in request.query_params.getlist("company") if c.strip()))


def library_filters(request: Request) -> db.LibraryFilters:
    """The library's filters from the query string (the Ads screen and its CSV export)."""
    q = request.query_params
    days = q.get("min_days", "").strip()
    return db.LibraryFilters(
        companies=selected_companies(request), media=q.get("media", ""),
        status=q.get("status", "active"), min_days=int(days) if days.isdigit() else 0,
        q=q.get("q", ""), collected_app=q.get('collected_app',''),
    )


def _library_payload(request: Request) -> dict:
    filters = library_filters(request)
    page = request.query_params.get("page", "0")
    return {
        **db.get_library_page(filters, sort=request.query_params.get("sort", "launch"),
                              page=int(page) if page.isdigit() else 0),
        "champions": [] if filters.collected_app else db.get_champions(filters.companies),
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _authed(request):
        if _needs_setup():
            return RedirectResponse("/setup", status_code=302)
        if request.url.query:
            return RedirectResponse(
                "/login?next=" + quote("/?" + request.url.query, safe=""), status_code=302
            )
        return RedirectResponse("/login", status_code=302)
    if _setup_pending():
        return RedirectResponse("/setup/competitor", status_code=302)
    companies = db.get_library_companies()
    selected = selected_companies(request)
    initial = {**_library_payload(request), "has_ads": bool(companies)}
    data_json = json.dumps(initial, default=str).replace("</", "<\\/")
    apps = list_collected_apps()
    app_key = request.query_params.get('collected_app','').strip().lower()
    selected_app = next((app for app in apps if app['key']==app_key), None)
    return templates.TemplateResponse(request, "ads.html", {
        "data_json": data_json,
        'collected_apps': apps[:6], 'collected_apps_total': len(apps),
        'selected_app': selected_app, 'app_key': app_key,
        "company_choices": [{"value": name, "name": name, "checked": name in selected}
                            for name in companies],
    }, headers={"cache-control": "no-store"})


@router.get('/collected-apps', response_class=HTMLResponse)
def collected_apps_home(request: Request):
    if not _authed(request):
        return RedirectResponse('/login?next=/collected-apps', status_code=302)
    return templates.TemplateResponse(request, 'collected_apps.html', {
        'collected_apps': list_collected_apps(),
    }, headers={'Cache-Control': 'no-store'})


@router.get("/api/ads")
def library_api(request: Request):
    """One page of the library for the Ads screen's filters, sort and page."""
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(jsonable_encoder(_library_payload(request)),
                        headers={"cache-control": "no-store"})


@router.get("/api/ads/summary")
def library_summary_api(request: Request):
    """Public aggregate statistics for the same filters as the library."""
    return JSONResponse(jsonable_encoder(db.get_library_summary(library_filters(request))),
                        headers={"cache-control": "no-store"})


@router.get("/ads/{ad_archive_id}", response_class=HTMLResponse)
def ad_detail(request: Request, ad_archive_id: str):
    if not _authed(request):
        return RedirectResponse(
            "/login?next=" + quote("/ads/" + ad_archive_id, safe=""), status_code=302
        )
    ad = db.get_ad_detail(ad_archive_id)
    if ad is None:
        message = translate(request_lang(request), N_("Ad not found."))
        return HTMLResponse(f"<p>{html.escape(message)}</p>", status_code=404)

    with db.connect() as conn:
        annotation = conn.execute("SELECT favorite,tags,notes FROM l2_ad_annotations WHERE ad_archive_id=%s", (ad_archive_id,)).fetchone() or {'favorite':False,'tags':'','notes':''}
    creatives = ad["creatives"]
    title = next((c["title"] for c in creatives if c["title"]), "")
    from ...db.metadata import coverage_details
    coverage_regions = coverage_details(ad_archive_id)
    # Public editorial summaries are limited to exact, sourced drama titles.
    synopses = (
        ("mon époux a fait s'incliner le ciel", '为逃避包办婚姻、保护双胞胎，六翼天使寻找隐居人间的魔王。', 'https://dramafinds.com/fr/detail/260011/mon-%C3%A9poux-a-fait-s-incliner-le-ciel'),
        ('stealing my sister’s lion king fiancé', '薇薇安发现秘密情人狮王即将迎娶继姐，在禁忌关系与家庭冲突中主动出击。', 'https://www.reelviral.net/drama/dramawave/Bubr09ZCg0/'),
    )
    normalized_title = title.casefold().replace("'", "’").replace('é', 'e')
    synopsis = next(((summary, source) for name, summary, source in synopses
                     if normalized_title.startswith(name.casefold().replace("'", "’").replace('é', 'e'))), None)
    # the ad's own primary text; a card's text only when the ad has none
    body = ad["body"] or next((c["body"] for c in creatives if c["body"]), "")
    cta = next((c["cta_text"] for c in creatives if c["cta_text"]), "")
    tier_class, tier_label = _tier(ad["duration_days"])
    # Platform names are Meta's product names (Facebook, Instagram…): not translated.
    platforms_label = " · ".join(
        p.replace("_", " ").title() for p in ad["platforms"]
    )
    # The template shows sort_index, the ad's rank in Meta's impression-sorted
    # response for its own page: per advertiser, never a global position.
    return templates.TemplateResponse(request, "detail.html", {
        "ad": ad, "title": title, "body": body, "cta": cta,
        "annotation": annotation, "coverage_regions": coverage_regions, "synopsis": synopsis,
        "is_meta": ad["platform"] == "meta",
        "tier_class": tier_class, "tier_label": tier_label,
        "platforms_label": platforms_label,
        "likes_label": compact_number(ad["page"]["like_count"], request_lang(request)),
    })


def compact_number(n: int | None, lang: str) -> str:
    """39538110 -> '39.5M' (',' as the decimal mark outside English); '' if None."""
    if n is None:
        return ""
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= size:
            value = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return (value if lang == "en" else value.replace(".", ",")) + suffix
    return str(n)

@router.post("/ads/{ad_archive_id}/annotation")
def save_annotation(request: Request, ad_archive_id: str, tags: str = Form(""), notes: str = Form(""), favorite: str = Form("")):
    if not _authed(request):
        return JSONResponse({"error":"unauthorized"}, status_code=401)
    from urllib.parse import urlparse
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        return JSONResponse({"error":"invalid origin"}, status_code=403)
    if len(tags)>300 or len(notes)>5000:
        return JSONResponse({"error":"标签或备注过长"}, status_code=422)
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM ads WHERE ad_archive_id=%s",(ad_archive_id,)).fetchone():
            return JSONResponse({"error":"广告不存在"}, status_code=404)
        conn.execute("INSERT INTO l2_ad_annotations(ad_archive_id,favorite,tags,notes,updated_at) "
          "VALUES (%s,%s,%s,%s,now()) ON CONFLICT (ad_archive_id) DO UPDATE "
          "SET favorite=EXCLUDED.favorite,tags=EXCLUDED.tags,notes=EXCLUDED.notes,updated_at=now()",
          (ad_archive_id,favorite=="on",tags,notes))
    return RedirectResponse("/ads/"+quote(ad_archive_id,safe="")+"?annotation_saved=1#research-notes",status_code=303)

@router.post("/ads/{ad_archive_id}/refresh-coverage")
def refresh_coverage_from_detail(request: Request, ad_archive_id: str):
    if not _authed(request):
        return JSONResponse({"error":"unauthorized"},status_code=401)
    from urllib.parse import urlparse
    origin=request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        return JSONResponse({"error":"invalid origin"},status_code=403)
    from ...worker.disclosures import refresh_ads
    import asyncio
    with db.connect() as conn:
        row=conn.execute(
            "SELECT id,ad_archive_id,page_id,platform FROM ads WHERE ad_archive_id=%s",
            (ad_archive_id,),
        ).fetchone()
    if not row:
        return JSONResponse({"error":"广告不存在"},status_code=404)
    if row["platform"] != "meta":
        return JSONResponse({"error":"只有 Meta 广告支持重新读取公开覆盖详情"},status_code=409)
    result=asyncio.run(refresh_ads([row]))
    status="failed" if result.get("failed") else "done"
    return RedirectResponse("/ads/"+quote(ad_archive_id,safe="")+"?coverage_refresh="+status+"#coverage-refresh",status_code=303)

@router.get("/insights",response_class=HTMLResponse)
def own_title_insights(request: Request):
    if not _authed(request):
        return RedirectResponse("/login?next=/insights",status_code=302)
    from ...db.metadata import metadata_for_ads
    from datetime import timezone
    with db.connect() as conn:
        groups=conn.execute(
          "WITH titled AS (SELECT DISTINCT a.id,a.ad_archive_id,a.is_active,a.first_seen_at,a.last_seen_at,btrim(cr.title) AS title "
          "FROM ads a JOIN creatives cr ON cr.ad_id=a.id WHERE length(btrim(cr.title))>=12 AND strpos(cr.title,'{{')=0 "
          "AND lower(btrim(cr.title)) NOT IN ('continue watch','watch more for free!','watch the full story','watch the full story 👉','download now')) "
          "SELECT title, count(*) AS ad_count, count(*) FILTER (WHERE is_active IS TRUE) AS active_count, "
          "count(*) FILTER (WHERE first_seen_at >= now()-interval '7 days') AS new7_count, max(last_seen_at) AS last_seen, "
          "(array_agg(ad_archive_id ORDER BY last_seen_at DESC NULLS LAST,ad_archive_id))[1:3] AS sample_ids "
          "FROM titled GROUP BY title ORDER BY ad_count DESC,max(last_seen_at) DESC NULLS LAST,title LIMIT 60"
        ).fetchall()
    metadata=metadata_for_ads(list({ad for g in groups for ad in g['sample_ids']}))
    for g in groups:
        g['country_names']=sorted({name for ad in g['sample_ids'] for name in metadata.get(ad,{}).get('coverage_country_names',[])})
        g['last_seen']=g['last_seen'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M') if g['last_seen'] else None
    return templates.TemplateResponse(request,'insights.html',{'groups':groups},headers={'cache-control':'no-store'})

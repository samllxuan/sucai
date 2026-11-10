"""One shape for a collected Ad, whatever the Collection source.

Meta's sources hand us the same thing underneath: Meta's Ad Library record for one
ad (a `collated_results[]` node, with the creative under `snapshot`).
  - Self-hosted: `meta_ads_collector`'s `Ad` keeps that record in `ad.raw_data`
    (the snapshot keys are also copied to the top level).
  - Apify: each dataset item *is* that record, with `snapshot` nested.

Meta adapters go through one parser of the record, `from_meta_record`, and
`persist.upsert_ad` only ever sees a `NormalizedAd`. The library's own model is a
fallback for an `Ad` without `raw_data`; it drops fields we need
(display_format, caption, the Page's picture/likes/categories).

TikTok's public web API has a different shape, parsed by `from_tiktok_record`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class NormalizedCreative:
    """One card of an Ad (or its single creative). `image_url` is the full-size
    upload (Meta's 600px copy only when there is none; for a carousel it can even be
    a different crop). `card_key` identifies the card across reads (see `card_key`)."""
    body: str | None = None
    title: str | None = None
    description: str | None = None
    caption: str | None = None
    link_url: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    video_hd_url: str | None = None
    video_sd_url: str | None = None
    thumbnail_url: str | None = None
    cta_text: str | None = None
    cta_type: str | None = None
    card_key: str | None = None


@dataclass
class NormalizedPage:
    id: str | None = None
    name: str | None = None
    profile_picture_url: str | None = None
    profile_uri: str | None = None
    like_count: int | None = None
    categories: list[str] = field(default_factory=list)


@dataclass
class NormalizedAd:
    id: str
    page: NormalizedPage
    platform: str = "meta"
    external_ad_id: str | None = None
    is_active: bool | None = None
    ad_status: str | None = None
    delivery_start_time: datetime | None = None
    delivery_stop_time: datetime | None = None
    snapshot_url: str | None = None
    ad_snapshot_url: str | None = None
    publisher_platforms: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    ad_type: str | None = None
    categories: list[str] = field(default_factory=list)
    collation_id: str | None = None
    collation_count: int | None = None
    display_format: str | None = None
    body: str | None = None   # the ad's primary text (cards may have their own)
    creatives: list[NormalizedCreative] = field(default_factory=list)
    search_country: str | None = None  # observation scope, never complete coverage
    reported_countries: list[str] = field(default_factory=list)
    advertiser_name: str | None = None
    advertiser_id: str | None = None
    registry_location: str | None = None
    sponsor: str | None = None
    coverage_data: dict | None = None
    coverage_countries: list[str] = field(default_factory=list)
    coverage_status: str | None = None
    coverage_source: str | None = None
    coverage_checked_at: datetime | None = None


# ── Parsing Meta's record ───────────────────────────────────────────────────

def _text(value: Any) -> str | None:
    """Card `body` is a string; the ad-level one is `{"text": ...}`."""
    if isinstance(value, dict):
        value = value.get("text")
    return value if isinstance(value, str) else None


def _epoch(value: Any) -> datetime | None:
    """Epoch timestamps from Meta (seconds) or TikTok (milliseconds)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            epoch = float(value)
            if epoch > 10_000_000_000:
                epoch /= 1000
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None


def _card(card: dict, snap: dict) -> NormalizedCreative:
    hd, sd = card.get("video_hd_url"), card.get("video_sd_url")
    return NormalizedCreative(
        body=_text(card.get("body")),
        title=card.get("title"),
        description=card.get("link_description"),
        caption=card.get("caption") or snap.get("caption"),
        link_url=card.get("link_url"),
        image_url=card.get("original_image_url") or card.get("resized_image_url"),
        video_url=hd or sd,
        video_hd_url=hd,
        video_sd_url=sd,
        thumbnail_url=card.get("video_preview_image_url"),
        cta_text=card.get("cta_text") or snap.get("cta_text"),
        cta_type=card.get("cta_type"),
    )


def _single(snap: dict) -> NormalizedCreative | None:
    """An ad without cards: one creative from the ad-level fields and the first
    image/video (the rest are not used, as before)."""
    if not any(snap.get(k) is not None for k in ("body", "title", "videos", "images")):
        return None
    video = (snap.get("videos") or [{}])[0] or {}
    image = (snap.get("images") or [{}])[0] or {}
    hd, sd = video.get("video_hd_url"), video.get("video_sd_url")
    return NormalizedCreative(
        body=_text(snap.get("body")),
        title=snap.get("title"),
        description=snap.get("link_description"),
        caption=snap.get("caption"),
        link_url=snap.get("link_url"),
        image_url=image.get("original_image_url") or image.get("resized_image_url"),
        video_url=hd or sd,
        video_hd_url=hd,
        video_sd_url=sd,
        thumbnail_url=video.get("video_preview_image_url"),
        cta_text=snap.get("cta_text"),
        cta_type=snap.get("cta_type"),
    )


def from_meta_record(rec: dict) -> NormalizedAd:
    """Parse one Ad Library record (Apify item, or the library's `raw_data`)."""
    from .disclosures import country_codes
    snap = rec.get("snapshot") or rec
    cards = snap.get("cards") or []
    if cards:
        creatives = [_card(c, snap) for c in cards]
    else:
        single = _single(snap)
        creatives = [single] if single else []

    platforms = rec.get("publisher_platform") or rec.get("publisher_platforms") or []
    if isinstance(platforms, str):
        platforms = [platforms]
    is_active = rec.get("is_active")
    status = rec.get("ad_status")
    if is_active is None and status:
        is_active = status == "ACTIVE"

    return NormalizedAd(
        id=str(rec.get("ad_archive_id") or rec.get("id") or ""),
        platform="meta",
        external_ad_id=str(rec.get("ad_archive_id") or rec.get("id") or "") or None,
        page=NormalizedPage(
            id=str(rec.get("page_id") or snap.get("page_id") or "") or None,
            name=rec.get("page_name") or snap.get("page_name"),
            profile_picture_url=snap.get("page_profile_picture_url"),
            profile_uri=snap.get("page_profile_uri"),
            like_count=snap.get("page_like_count"),
            categories=list(snap.get("page_categories") or []),
        ),
        is_active=is_active,
        ad_status=status,
        delivery_start_time=_epoch(rec.get("start_date")),
        # Meta sets an active ad's end_date to the day it was read: not an end.
        delivery_stop_time=None if is_active else _epoch(rec.get("end_date")),
        snapshot_url=rec.get("snapshot_url"),
        ad_snapshot_url=rec.get("ad_snapshot_url"),
        publisher_platforms=list(platforms),
        languages=list(rec.get("languages") or []),
        ad_type=rec.get("ad_type"),
        categories=list(rec.get("categories") or []),
        collation_id=rec.get("collation_id") or None,
        collation_count=rec.get("collation_count"),
        display_format=snap.get("display_format") or None,
        body=_text(snap.get("body")),
        reported_countries=country_codes(rec.get('targeted_or_reached_countries') or []),
        creatives=_with_card_keys(creatives),
    )


# ── Adapters ────────────────────────────────────────────────────────────────

def from_apify_item(item: dict) -> NormalizedAd:
    """An item of the Apify Actor's dataset. Extra keys the Actor adds
    (`daysRunning`, `url`, `_details`) are ignored: we compute our own duration."""
    return from_meta_record(item)


def from_library_ad(ad) -> NormalizedAd:
    """A `meta_ads_collector` `Ad` (Self-hosted source)."""
    raw = getattr(ad, "raw_data", None)
    if isinstance(raw, dict) and (raw.get("ad_archive_id") or raw.get("id")):
        return from_meta_record(raw)
    return _from_library_model(ad)


def _from_library_model(ad) -> NormalizedAd:
    page = getattr(ad, "page", None)
    return NormalizedAd(
        id=str(ad.id),
        platform="meta",
        external_ad_id=str(ad.id),
        page=NormalizedPage(
            id=getattr(page, "id", None) or None,
            name=getattr(page, "name", None) or None,
            profile_picture_url=getattr(page, "profile_picture_url", None),
            profile_uri=getattr(page, "page_url", None),
            like_count=getattr(page, "likes", None),
        ),
        is_active=ad.is_active,
        ad_status=ad.ad_status,
        delivery_start_time=ad.delivery_start_time,
        delivery_stop_time=None if ad.is_active else ad.delivery_stop_time,
        snapshot_url=ad.snapshot_url,
        ad_snapshot_url=ad.ad_snapshot_url,
        publisher_platforms=list(ad.publisher_platforms or []),
        languages=list(ad.languages or []),
        ad_type=ad.ad_type,
        categories=list(ad.categories or []),
        collation_id=getattr(ad, "collation_id", None) or None,
        collation_count=getattr(ad, "collation_count", None),
        creatives=_with_card_keys([
            NormalizedCreative(
                body=c.body, title=c.title, description=c.description,
                caption=getattr(c, "caption", None), link_url=c.link_url,
                image_url=c.image_url,
                video_url=c.video_url, video_hd_url=c.video_hd_url,
                video_sd_url=c.video_sd_url, thumbnail_url=c.thumbnail_url,
                cta_text=c.cta_text, cta_type=c.cta_type,
            )
            for c in (ad.creatives or [])
        ]),
    )


# ── Parsing TikTok's public web API ─────────────────────────────────────────

def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _url(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        for key in ("url", "video_url", "cover_url", "image_url", "display_url"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        digits = value.strip().replace(",", "")
        if digits.isdigit():
            return int(digits)
    return None


def _tiktok_advertiser_id(advertiser: dict) -> str | None:
    value = (advertiser.get("adv_biz_ids") or advertiser.get("adv_biz_id")
             or advertiser.get("business_id"))
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, (str, int)) and str(v).strip()), None)
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip()
    return None


def _tiktok_page_id(advertiser: dict, name: str | None) -> str:
    advertiser_id = _tiktok_advertiser_id(advertiser)
    if advertiser_id:
        return f"tt_advertiser:{advertiser_id}"
    if name:
        import hashlib
        digest = hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:16]
        return f"tt_advertiser:{digest}"
    return "tt_advertiser:unknown"


def _tiktok_creative(ad: dict, coverage: dict | None) -> NormalizedCreative | None:
    videos = ad.get("videos") or []
    images = ad.get("image_urls") or ad.get("images") or []
    video = _first(videos)
    image = _first(images)
    video_url = _url(video)
    thumbnail_url = (
        _url(video.get("cover_img"))
        if isinstance(video, dict) else None
    )
    image_url = _url(image)
    body = None
    for value in (
        ad.get("title"),
        ad.get("description"),
    ):
        if isinstance(value, str) and value.strip():
            body = value
            break
    title = ad.get("title")
    link_url = ad.get("external_url") if isinstance(ad.get("external_url"), str) else None
    if not any((body, title, image_url, video_url, thumbnail_url, link_url)):
        return None
    return NormalizedCreative(
        body=body if isinstance(body, str) else None,
        title=title if isinstance(title, str) else None,
        description=(ad.get("description") if isinstance(ad.get("description"), str) else None),
        caption=(ad.get("sponsor") if isinstance(ad.get("sponsor"), str) else None),
        link_url=link_url,
        image_url=image_url,
        video_url=video_url,
        video_hd_url=video_url,
        thumbnail_url=thumbnail_url,
        cta_text=(ad.get("call_to_action") or ad.get("cta")),
        cta_type=ad.get("advertising_objective"),
    )


def _tiktok_coverage(detail: dict) -> tuple[dict, list[str]]:
    data = detail.get("data") or {}
    ad = data.get("ad") or {}
    targeting = data.get("targeting") or {}
    location = targeting.get("location") or {}
    locations = location.get("data") or []
    countries: set[str] = set()
    regions = []
    for location_row in locations if isinstance(locations, list) else []:
        if not isinstance(location_row, dict):
            continue
        code = str(location_row.get("region") or location_row.get("country") or "").upper()
        if len(code) != 2:
            continue
        countries.add(code)
        rows = []
        for breakdown in location_row.get("breakdowns") or []:
            if not isinstance(breakdown, dict):
                continue
            gender = str(breakdown.get("gender") or "").upper()
            raw_reach = breakdown.get("impressions") or breakdown.get("reach")
            reach = _number(raw_reach)
            if reach is None and not (
                isinstance(raw_reach, str) and raw_reach.strip()
            ):
                continue
            row = {"country": code, "age": str(breakdown.get("age") or "未注明"),
                   "male": None, "female": None, "unknown": None}
            key = {"MALE": "male", "FEMALE": "female"}.get(gender, "unknown")
            row[key] = reach if reach is not None else raw_reach.strip()
            rows.append(row)
        raw_locations_reach = location_row.get("impressions")
        reach = _number(raw_locations_reach)
        if reach is None and isinstance(raw_locations_reach, str) and raw_locations_reach.strip():
            reach = raw_locations_reach.strip()
        regions.append({"region": code, "reach": reach, "rows": rows})
    raw_total_impressions = (
        ad.get("total_impressions")
        or location.get("total_impressions")
        or ad.get("estimated_audience")
    )
    total_impressions = _number(raw_total_impressions)
    if total_impressions is None and isinstance(raw_total_impressions, str):
        total_impressions = raw_total_impressions.strip() or None
    raw = {
        "estimated_audience": ad.get("estimated_audience"),
        "total_impressions": total_impressions,
        "location_total_region": location.get("total_region"),
        "locations": locations,
    }
    if regions:
        raw["regions"] = regions
    return raw, sorted(countries)


def from_tiktok_record(detail: dict) -> NormalizedAd:
    """Parse a TikTok Ad Library item-details response."""
    data = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(data, dict):
        raise ValueError("TikTok 详情缺少 data")
    ad = data.get("ad")
    advertiser = data.get("advertiser")
    if not isinstance(ad, dict) or not isinstance(advertiser, dict):
        raise ValueError("TikTok 详情缺少广告或广告主")
    external_id = str(ad.get("id") or "").strip()
    if not external_id.isdigit():
        raise ValueError("TikTok 详情缺少有效广告 ID")
    advertiser_name = (advertiser.get("name") or advertiser.get("display_name")
                       or ad.get("advertiser_name"))
    if not isinstance(advertiser_name, str):
        advertiser_name = None
    page_id = _tiktok_page_id(advertiser, advertiser_name)
    coverage_data, countries = _tiktok_coverage(detail)
    creative = _tiktok_creative(ad, coverage_data)
    first_shown = _epoch(ad.get("first_shown_date"))
    last_shown = _epoch(ad.get("last_shown_date"))
    status = "ACTIVE"
    is_active = True
    title = ad.get("title") if isinstance(ad.get("title"), str) else None
    body = title or (ad.get("name") if isinstance(ad.get("name"), str) else None)
    checked_at = datetime.now(timezone.utc)
    return NormalizedAd(
        id=f"tt_{external_id}",
        platform="tiktok",
        external_ad_id=external_id,
        page=NormalizedPage(
            id=page_id,
            name=advertiser_name,
            profile_picture_url=_url(advertiser.get("avatar_url") or advertiser.get("logo_url")),
            profile_uri=advertiser.get("profile_url"),
        ),
        is_active=is_active,
        ad_status=status or None,
        delivery_start_time=first_shown,
        delivery_stop_time=None,
        snapshot_url=f"https://library.tiktok.com/ads/detail/?ad_id={external_id}",
        publisher_platforms=["TikTok"],
        ad_type=ad.get("advertising_objective"),
        body=body,
        creatives=_with_card_keys([creative] if creative else []),
        reported_countries=countries,
        advertiser_name=advertiser_name,
        advertiser_id=_tiktok_advertiser_id(advertiser),
        registry_location=(advertiser.get("registry_location")
                           if isinstance(advertiser.get("registry_location"), str) else None),
        sponsor=(advertiser.get("sponsor") if isinstance(advertiser.get("sponsor"), str) else None),
        coverage_data=coverage_data,
        coverage_countries=countries,
        coverage_status="available",
        coverage_source="tiktok_ad_library",
        coverage_checked_at=checked_at,
    )


# ── Card identity ───────────────────────────────────────────────────────────
# Meta reorders an Ad's cards between reads, so a card's position can't identify
# it. Its media file can: the file name in the fbcdn path is the same on every
# read, whichever CDN host served it and whatever the expiring query (`oe=`...).

_MEDIA_NAME = re.compile(r"/([^/?#]+)\.(?:jpe?g|png|webp|gif|mp4)(?:[?#]|$)", re.I)


def media_id(url: str | None) -> str:
    """The media file's name in its URL ('' if none), stable across reads."""
    match = _MEDIA_NAME.search(url or "")
    return match.group(1) if match else ""


def _link_key(url: str | None) -> str:
    """A destination without its query string/fragment (per-placement UTM tags)."""
    return (url or "").split("#", 1)[0].split("?", 1)[0]


def card_key(creative: NormalizedCreative) -> str:
    """Media file id + destination. A video card is keyed by its preview image
    (the video's own URL is an opaque token)."""
    media = (media_id(creative.image_url) or media_id(creative.thumbnail_url)
             or media_id(creative.video_url))
    return f"{media}|{_link_key(creative.link_url)}"


def _with_card_keys(creatives: list[NormalizedCreative]) -> list[NormalizedCreative]:
    """Set each card's key; a repeated key (same media and destination) gets its
    occurrence number, so every card of the Ad has its own."""
    seen: dict[str, int] = {}
    for creative in creatives:
        key = card_key(creative)
        n = seen.get(key, 0)
        seen[key] = n + 1
        creative.card_key = key if n == 0 else f"{key}#{n}"
    return creatives

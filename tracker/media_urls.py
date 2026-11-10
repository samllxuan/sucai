"""Which media URLs the app may fetch: Meta's CDN, over https.

Media URLs come from collected ad data. With the Apify Collection source that data
is produced by an Actor running outside this instance, so a URL in it can point
anywhere, including this server's own network. Both places that fetch media check
it here: the worker when it mirrors media to storage, and the web app's /media
route when it falls back to the original URL.
"""

from __future__ import annotations

from urllib.parse import urlparse

META_MEDIA_HOSTS = ("fbcdn.net", "cdninstagram.com", "fbsbx.com", "facebook.com")
TIKTOK_MEDIA_HOSTS = (
    "library.tiktok.com",
    "p16-common-sign.tiktokcdn.com",
    "p19-common-sign.tiktokcdn.com",
)
MEDIA_HOSTS = META_MEDIA_HOSTS + TIKTOK_MEDIA_HOSTS


def is_meta_media_url(url: str | None) -> bool:
    """An https URL on one of Meta's media hosts (or a subdomain of one)."""
    return _is_media_url(url, META_MEDIA_HOSTS)


def is_allowed_media_url(url: str | None) -> bool:
    """An https URL on a known Meta or TikTok media host."""
    return _is_media_url(url, MEDIA_HOSTS)


def _is_media_url(url: str | None, hosts: tuple[str, ...]) -> bool:
    try:
        p = urlparse(url or "")
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in hosts)

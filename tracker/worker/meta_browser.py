"""Read public Ad Library search results through an isolated browser.

Only public ad response objects are retained; never persist HTML, cookies or
request tokens. No login, challenge solving or personal browser profile.
"""
import asyncio
import json
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit, unquote
from ..collection_window import in_launch_window, launch_date




class BrowserSearchError(ValueError):
    pass


def script_payloads(html):
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active = False
            self.parts = []
            self.payloads = []
        def handle_starttag(self, tag, attrs):
            if tag == 'script':
                self.active = dict(attrs).get('type') == 'application/json'
                self.parts = []
        def handle_data(self, data):
            if self.active:
                self.parts.append(data)
        def handle_endtag(self, tag):
            if tag == 'script' and self.active:
                try:
                    self.payloads.append(json.loads(''.join(self.parts)))
                except ValueError:
                    pass
                self.active = False
    parser = Scripts()
    parser.feed(html)
    return parser.payloads


def search_url(query, country, kind, ad_status, search_mode, *, launch_from=None, launch_to=None, sort='default'):
    params = {'active_status': ad_status.lower(), 'ad_type': 'all', 'country': country}
    if kind == 'page':
        params.update(view_all_page_id=query, search_type='page')
    else:
        params.update(q=query, search_type='keyword_exact_phrase' if search_mode == 'exact' else 'keyword_unordered')
    if sort == 'newest':
        params['sort_data[mode]'] = 'relevancy_monthly_grouped'
        params['sort_data[direction]'] = 'desc'
    if launch_from:
        params['start_date[min]'] = launch_from
    if launch_to:
        params['start_date[max]'] = launch_to
    return 'https://www.facebook.com/ads/library/?' + urlencode(params)


def extract_results(payload):
    """Accept only search connections, not unrelated ads elsewhere in Relay data."""
    ads = {}
    found = False
    has_more = None

    def visit(value):
        nonlocal found, has_more
        if isinstance(value, dict):
            connection = value.get('search_results_connection')
            if isinstance(connection, dict) and isinstance(connection.get('edges'), list):
                info = connection.get('page_info') or {}
                if isinstance(info.get('has_next_page'), bool) or connection['edges']:
                    found = True
                    has_more = info.get('has_next_page')
                    for edge in connection['edges']:
                        for ad in (edge.get('node') or {}).get('collated_results') or []:
                            ad_id = str(ad.get('ad_archive_id') or '')
                            if ad_id.isdigit() and isinstance(ad.get('snapshot'), dict):
                                ads[ad_id] = ad
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    return list(ads.values()), found, has_more


class MetaBrowser:
    def __init__(self, proxy=None):
        self.runtime = self.browser = self.context = None
        self.proxy = proxy

    async def close(self):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.runtime:
                await self.runtime.stop()

    async def search(self, query, country, limit, kind, ad_status, search_mode, *,
                     launch_from=None, launch_to=None, sort='default'):
        from playwright.async_api import async_playwright, Error as PlaywrightError
        if not self.browser:
            self.runtime = await async_playwright().start()
            options = {'headless': True}
            if self.proxy:
                parsed = urlsplit(self.proxy)
                # Credentials are passed privately to Chromium, never to a URL
                # in the UI or a collection record.
                host = parsed.hostname or ''
                if ':' in host:
                    host = '[' + host + ']'
                server = parsed.scheme + '://' + host + (':' + str(parsed.port) if parsed.port else '')
                options['proxy'] = {'server': server}
                if parsed.username:
                    options['proxy']['username'] = unquote(parsed.username)
                if parsed.password:
                    options['proxy']['password'] = unquote(parsed.password)
            self.browser = await self.runtime.chromium.launch(**options)
            self.context = await self.browser.new_context(locale='en-US')
        page = await self.context.new_page()
        ads = {}
        found = False
        has_more = None
        tasks = set()

        def ingest(payload):
            nonlocal found, has_more
            records, valid, more = extract_results(payload)
            if valid:
                found = True
                has_more = more
                ads.update((str(ad['ad_archive_id']), ad) for ad in records)

        async def read_response(response):
            try:
                if response.request.resource_type == 'document' and response.url.startswith('https://www.facebook.com/ads/library/'):
                    for payload in script_payloads(await response.text()):
                        ingest(payload)
                    return
                if '/api/graphql' not in response.url:
                    return
                # Retain only result objects, never request headers/form data.
                for line in (await response.text()).removeprefix('for (;;);').splitlines():
                    try:
                        ingest(json.loads(line))
                    except ValueError:
                        pass
            except Exception:
                pass  # Missing valid result data below is a failure, never an empty result.

        def response_received(response):
            task = asyncio.create_task(read_response(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        page.on('response', response_received)
        try:
            # Some Meta responses use HTTP 403 while returning a renderable search
            # page. Success requires a real structured search connection below.
            await page.goto(search_url(query, country, kind, ad_status, search_mode,
                                      launch_from=launch_from, launch_to=launch_to, sort=sort),
                            wait_until='domcontentloaded', timeout=45000)
            for attempt in range(8):
                try:
                    scripts = await page.locator('script[type="application/json"]').all_text_contents()
                except PlaywrightError as exc:
                    # Meta may navigate once more after DOMContentLoaded.
                    if 'Execution context was destroyed' not in str(exc):
                        raise
                    await page.wait_for_timeout(1000)
                    continue
                for script in scripts:
                    try:
                        ingest(json.loads(script))
                    except ValueError:
                        pass
                if found:
                    break
                await page.wait_for_timeout(1000)
            if not found:
                raise BrowserSearchError('Meta 浏览器未返回有效搜索结果；可能访问受限或页面结构变化，不视为空结果')
            def eligible():
                return [ad for ad in ads.values() if in_launch_window(ad, launch_from, launch_to)]
            for _ in range(4):
                # Old rows do not consume the requested recent-ad sample limit.
                if len(eligible()) >= limit or has_more is False:
                    break
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(2500)
            if tasks:
                await asyncio.gather(*list(tasks))
            records = list(ads.values())
            if kind == 'page' and any(str(ad.get('page_id')) != str(query) for ad in records):
                raise BrowserSearchError('Meta 网页返回的主页与请求不一致；未保存此批素材')
            # Unknown pagination and stalled lazy loading must never count as complete.
            selected = eligible()
            if sort == 'newest':
                selected.sort(key=lambda ad: launch_date(ad) or '', reverse=True)
            missing_dates = (launch_from or launch_to) and any(launch_date(ad) is None for ad in records)
            complete = has_more is False and len(selected) <= limit and not missing_dates
            return selected[:limit], bool(complete)
        finally:
            page.remove_listener('response', response_received)
            for task in list(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*list(tasks), return_exceptions=True)
            await page.close()

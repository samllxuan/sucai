"""Shared public-browser collection scopes; a search region is not ad coverage."""

EUROPE = tuple('GB IE FR DE IT ES PT NL BE LU AT CH LI DK SE NO FI IS PL CZ SK HU RO BG GR HR SI EE LV LT MT CY AL AD BA ME MK RS MD UA BY RU MC SM VA'.split())
REGIONS = {'EUROPE': ('欧洲', EUROPE)}


def resolve_scope(value, valid_countries):
    """Return (region or None, ISO-2 fallback) without storing a region as a country."""
    value = value.strip().upper()
    if value in REGIONS:
        return value, REGIONS[value][1][0]
    if value not in valid_countries:
        raise ValueError('请选择有效国家或区域')
    return None, value


def browser_targets(pages, settings):
    mode = settings.get('collect_mode', 'pages')
    country = settings.get('collect_country') or 'US'
    region = settings.get('collect_region')
    if region and region not in REGIONS:
        raise ValueError('采集区域无效，请重新选择')
    countries = REGIONS[region][1] if region else (country,)
    if mode == 'keyword':
        keyword = settings.get('collect_keyword', '').strip()
        if not keyword:
            raise ValueError('请先输入 App 名称或关键词')
        return [(keyword, code, 'keyword', None) for code in countries]
    if not pages:
        raise ValueError('未选择采集主页，请到“开始采集”选择主页或输入 App 名称')
    # Keep each Page's country for ordinary searches; a region overrides it for this run.
    return [(pg['page_id'], code if region else pg.get('collect_country') or code, 'page', pg)
            for code in countries for pg in pages]

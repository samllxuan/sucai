# L2 合作平台 API（v1）

地址：`https://l2.divinlove.com`。请求和响应均为 UTF-8 JSON。除公开的 `GET /api/ads/summary` 外，接口必须通过请求头 `Authorization: Bearer <API密钥>` 认证；密钥由 L2 管理者另行提供，请勿提交到 Git、写进网址或前端代码。网页免登录不等于接口免密钥。

例如，密钥已安全存入服务端环境变量 `L2_PARTNER_KEY` 时：

```bash
curl -H "Authorization: Bearer $L2_PARTNER_KEY" \
  "https://l2.divinlove.com/api/v1/ads?limit=50"
curl -X POST "https://l2.divinlove.com/api/v1/searches" \
  -H "Authorization: Bearer $L2_PARTNER_KEY" -H "Content-Type: application/json" \
  -d '{"title":"I Got Three Girlfriends From the System","country":"US"}'
```

## 搜索剧目并在缺失时采集

`POST /api/v1/searches`

```json
{"title":"I Got Three Girlfriends From the System","country":"US"}
```

`country` 可省略，默认美国。系统先在 L2 中按完整文字匹配既有素材；这里的国家不限制已有素材的来源。如果找到，返回 `status: completed`、`source: l2_library` 和 `results`。结果按素材库逻辑合并同创意版本，`results.cards` 是当前页，`results.ad_count` 是匹配的广告记录数。

如果没有，返回 `status: queued`、`job_id`、`status_url`。后台按美国等指定国家、Meta 投放中、不限制上线日期、每次最多 30 条进行完整短语搜索；关键词需要出现在公开标题、文案或主页名称中，只有视频画面含剧名的素材可能无法命中。重复提交同一密钥、剧名和国家的排队/进行中任务会返回同一任务。队列已满返回 429。

`GET /api/v1/searches/{job_id}?page=0`

状态 `queued` 排队中、`running` 采集中、`completed` 有结果、`no_results` 本轮无结果、`failed` 失败。完成时 `results.cards` 为本次采集命中的广告记录，包括之前已存在的记录，支持从 0 开始的 `page`。记录中的 `id` 可用于调用素材详情接口。`partial: true` 表示分页、采集或后处理不完整。`no_results` 不代表 Meta 没有投放。任务失败或采集服务重启后需重新提交；不要将失败当成零结果。建议每 10–30 秒查询一次。

## 调研剧目在 TikTok、Google 短视频和 YouTube 的公开表现

`POST /api/v1/drama-research`

```json
{
  "title": "I Got Three Girlfriends From the System",
  "aliases": ["Three Girlfriends From the System", "系统给了我三个女朋友"],
  "region": "US"
}
```

`title` 必填；`aliases` 可为空，最多 10 个；`region` 是两位国家代码。API 返回 `202`、`job_id` 和 `status_url`，排队或运行中的相同密钥、剧名、别名集合和地区会复用同一任务。任务最多同时排队或运行 20 个，队列满时返回 `429`。

`status_url` 直接指向 `GET /api/ads/summary?status=all&q=<剧名>`，调用方每 10–30 秒轮询这里即可，不要再拼 `job_id`。采集完成后逐视频数据会直接落入数据库；summary 按剧名或别名找到同一剧目，并汇总所有国家已落库的视频。不需要也不使用 `drama_region`，同一个剧名无论从哪个国家任务采集，都会进入同一份汇总。没有匹配的短剧数据时仍返回原广告库 summary；存在短剧数据时额外返回 `drama_research`。

```json
{
  "total": 4,
  "total_reach": 30,
  "drama_research": {
    "status": "completed",
    "phase": "finished",
    "summary": {
      "tiktok": {"status": "complete", "video_count": 12, "views": 1234567, "likes": 98765, "comments": 4321, "shares": 678, "creator_count": 8},
      "google": {"status": "complete", "content_platform": "youtube", "discovery": "google_short_videos", "video_count": 7, "views": 765432, "likes": 45678, "comments": 2100, "shares": null, "creator_count": 5},
      "combined": {"platforms": ["google", "tiktok"], "video_count": 19, "views": 2000000, "likes": 144443, "comments": 6421, "shares": 678}
    },
    "collection": {"regions": ["GB", "US"], "region_count": 2, "captured_at": "2026-11-16T10:00:00+00:00", "warnings": [], "limits": {"max_videos_per_platform": 40, "time_budget_seconds": 600}},
    "updated_at": "2026-11-16T10:00:00+00:00"
  }
}
```

`drama_research.summary` 只返回所有国家的累计汇总数值，不含逐视频 `sources`、账号 `creators` 或搜索记录数组。同一视频在多个国家结果中出现时，按 `platform + video_id` 只计一次；播放、点赞、评论、分享分别取已采集到的最大值。因此重复采集、跨国家重复命中、样本变少或平台计数短暂回退都不会把已有统计改小。`collection.regions` 是已落库的国家列表，`captured_at` 取最近一次采集时间，`warnings` 合并各地区警告，`limits` 合并采集限制。

任务状态为 `queued`、`running`、`completed`、`partial`、`no_results` 或 `failed`；`phase` 依次可能为 `tiktok_discovery`、`tiktok_videos`、`google_discovery`、`youtube_videos`、`summarizing`。`partial` 表示至少拿到一部分视频，但某次搜索、视频详情或样本上限使结果不完整；`no_results` 只用于搜索页明确表示无结果；访问被限制或页面结构变化属于失败，不会伪报为零。

需要逐条排查视频来源时，可另外用鉴权接口 `GET /api/v1/drama-research/{job_id}` 查询完整详情；日常任务轮询只使用 summary。

采集逻辑：

- TikTok：按剧名和别名打开匿名搜索页，搜索页无视频卡片时再尝试对应 Discover 话题页；收集视频 URL 后逐条读取视频页公开指标。
- Google：使用 Google Short Videos 搜索 `"别名" youtube`，从结果中提取 YouTube 视频；不使用 Google Ads Transparency Center，也不把普通搜索结果伪装成“广告投放”。
- YouTube：逐条打开公开视频页，读取播放、点赞和可获得的评论数。Google 结果本身不是广告库，因此 `earliest_published_at` 是视频公开发布时间，不是首次投放时间。
- 全程串行访问，同一域名请求之间至少间隔：TikTok 2.5 秒、Google 2 秒、YouTube 1.25 秒。遇到 `429`、`500`、`502`、`503`、`504` 时最多重试 2 次，并按 `Retry-After` 或 5/10 秒指数退避；连续失败会转成 `partial`/`failed`，不会继续制造请求高峰。

完整详情的主要结构：

```json
{
  "job_id": "…",
  "status": "completed",
  "phase": "finished",
  "query": {"title": "…", "aliases": ["…"], "region": "US"},
  "summary": {
    "tiktok": {
      "status": "complete",
      "video_count": 12,
      "views": 1234567,
      "likes": 98765,
      "comments": 4321,
      "shares": 678,
      "creator_count": 8,
      "earliest_published_at": "2026-10-01T12:00:00+00:00",
      "latest_published_at": "2026-11-15T09:00:00+00:00",
      "metrics_available": {"views": 12, "likes": 12, "comments": 10, "shares": 9}
    },
    "google": {
      "status": "complete",
      "content_platform": "youtube",
      "discovery": "google_short_videos",
      "video_count": 7,
      "views": 765432,
      "likes": 45678,
      "comments": 2100,
      "shares": null,
      "creator_count": 5,
      "earliest_published_at": "2026-09-20T08:00:00+00:00",
      "latest_published_at": "2026-11-10T16:00:00+00:00",
      "metrics_available": {"views": 7, "likes": 6, "comments": 5, "shares": 0}
    },
    "combined": {
      "platforms": ["google", "tiktok"],
      "video_count": 19,
      "views": 2000000,
      "likes": 144443,
      "comments": 6421,
      "shares": 678
    }
  },
  "creators": {
    "tiktok": [{"creator": "account", "video_count": 2, "views": 120000, "likes": 8000, "comments": 300}],
    "google": [{"creator": "YouTube channel", "video_count": 1, "views": 90000, "likes": 5000, "comments": 120}]
  },
  "sources": [
    {
      "platform": "tiktok",
      "content_platform": "tiktok",
      "video_id": "7200000000000000001",
      "url": "https://www.tiktok.com/video/7200000000000000001",
      "title": "…",
      "creator": "account",
      "published_at": "2026-11-01T00:00:00+00:00",
      "views": 100000,
      "likes": 7000,
      "comments": 200,
      "shares": 30,
      "matched_aliases": ["…"]
    }
  ],
  "collection": {
    "region": "US",
    "aliases": ["…"],
    "captured_at": "2026-11-16T10:00:00+00:00",
    "searches": [{"platform": "tiktok", "alias": "…", "status": "ok", "candidate_count": 12}],
    "warnings": [],
    "limits": {
      "max_videos_per_platform": 40,
      "time_budget_seconds": 600,
      "serial_per_domain": true,
      "max_retries": 2
    }
  }
}
```

字段含义：

- `summary.*.video_count` 是实际成功读到公开指标的视频条数；`views`、`likes`、`comments`、`shares` 是这些视频对应字段的合计。平台不公开或页面未返回的字段记为 `null`，不能当成 0。`metrics_available` 说明每个合计覆盖了多少条视频，便于判断完整性。
- `earliest_published_at` / `latest_published_at` 是最早、最晚的视频发布时间，不是投放开始或停止日期。Google 结果没有广告曝光、花费、定向地区等数据。
- `sources` 只存在于完整详情接口，是逐条可追溯的视频明细，数量为实际采集成功的视频数（每个平台最多 40 条，整体最多 80 条）；它不是重复汇总。`matched_aliases` 表示该视频由哪些别名搜索到。
- `creators` 只存在于完整详情接口，是按账号/频道聚合的播放、点赞和评论。
- 完整详情中的 `collection.searches` 记录每次搜索候选数、状态和来源；summary 的 `collection` 只保留地区、采集时间、警告和限制，不返回搜索数组。

## 同步素材

`GET /api/v1/ads?limit=50` 返回最多 100 条，按素材 `last_seen_at` 和内部 ID 升序排列。返回 `items`、`snapshot_until`、`next_cursor`；有下一页时，将 `next_cursor` 原样传入 `cursor`。首次请求可使用带时区的 `updated_since`，例如 `2026-11-01T00:00:00Z`。完成一次同步后保存 `snapshot_until`，下一次以该时间作为 `updated_since`。采集重叠期间允许去重。此时间只跟踪素材观测，不包含独立的覆盖详情变动或删除事件；需要覆盖数据时定期重新取详情或进行全量对账。

`GET /api/v1/ads/{ad_id}` 返回单条广告、素材及 `coverage_regions`。国家、年龄、男性、女性和未知性别覆盖按 Meta 披露返回；`null` 表示未披露，不等于 0，数据来源和时间保留在 `public_metadata`。媒体可能是 L2 地址，也可能是 Meta 的临时地址，后者可能过期。

`GET /api/v1/charts` 返回 L2 已保存的 SocialPeta 日榜及 Short Drama Data 付费、免费和热剧榜快照。每条包含来源、原始榜单数据和本站读取时间；未保存的来源不会凭空生成。

常见状态码：401 无效密钥；400 游标或参数错误；404 任务/素材不存在；422 参数校验失败；429 队列已满；5xx 系统错误。错误不代表没有素材。API 仅开放 L2 已有或实际采集到的数据，不提供未披露的花费、点击率或完整全球覆盖。

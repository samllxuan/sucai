# L2 合作平台 API（v1）

地址：`https://l2.divinlove.com`。请求和响应均为 UTF-8 JSON。所有接口必须通过请求头 `Authorization: Bearer <API密钥>` 认证；密钥由 L2 管理者另行提供，请勿提交到 Git、写进网址或前端代码。网页免登录不等于此接口免密钥。

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

## 同步素材

`GET /api/v1/ads?limit=50` 返回最多 100 条，按素材 `last_seen_at` 和内部 ID 升序排列。返回 `items`、`snapshot_until`、`next_cursor`；有下一页时，将 `next_cursor` 原样传入 `cursor`。首次请求可使用带时区的 `updated_since`，例如 `2026-11-01T00:00:00Z`。完成一次同步后保存 `snapshot_until`，下一次以该时间作为 `updated_since`。采集重叠期间允许去重。此时间只跟踪素材观测，不包含独立的覆盖详情变动或删除事件；需要覆盖数据时定期重新取详情或进行全量对账。

`GET /api/v1/ads/{ad_id}` 返回单条广告、素材及 `coverage_regions`。国家、年龄、男性、女性和未知性别覆盖按 Meta 披露返回；`null` 表示未披露，不等于 0，数据来源和时间保留在 `public_metadata`。媒体可能是 L2 地址，也可能是 Meta 的临时地址，后者可能过期。

`GET /api/v1/charts` 返回 L2 已保存的 SocialPeta 日榜及 Short Drama Data 付费、免费和热剧榜快照。每条包含来源、原始榜单数据和本站读取时间；未保存的来源不会凭空生成。

常见状态码：401 无效密钥；400 游标或参数错误；404 任务/素材不存在；422 参数校验失败；429 队列已满；5xx 系统错误。错误不代表没有素材。API 仅开放 L2 已有或实际采集到的数据，不提供未披露的花费、点击率或完整全球覆盖。

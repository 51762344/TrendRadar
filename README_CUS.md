# TrendRadar for cus_bot_ui

本文档说明 TrendRadar 为 `cus_bot_ui` 提供 News 数据的 REST API 对接方式。

## 目标链路

```text
Browser -> cus_bot_ui frontend -> cus_bot_ui backend -> TrendRadar REST API -> TrendRadar data
```

约定：

- 浏览器不直接访问 TrendRadar。
- `cus_bot_ui` 后端是 TrendRadar API 的唯一调用方。
- TrendRadar 对外提供稳定 REST API，不要求 `cus_bot_ui` 直接对接 MCP。
- API 返回前端友好的 JSON 对象，不返回嵌套 JSON 字符串。

## 启动 API

TrendRadar 已提供独立 REST API 入口：

```bash
python -m trendradar.api
```

或安装后使用：

```bash
trendradar-api
```

常用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TRENDRADAR_API_HOST` | `0.0.0.0` | API 监听地址 |
| `TRENDRADAR_API_PORT` | `3334` | API 监听端口 |
| `TRENDRADAR_API_TOKEN` | 空 | Bearer Token |
| `TRENDRADAR_PROJECT_ROOT` | 当前目录 | TrendRadar 项目根目录 |
| `TRENDRADAR_API_CORS_ORIGIN` | 空 | 可选 CORS 来源 |

本地示例：

```bash
TRENDRADAR_API_TOKEN=dev-test-token \
TRENDRADAR_PROJECT_ROOT=/home/cuhp/projects/TrendRadar \
python -m trendradar.api --host 0.0.0.0 --port 3334
```

Zeabur 如果服务暴露的是 `8080`，直接改端口即可。最简单的 API 专用模式：

```bash
TRENDRADAR_API_TOKEN=<token> \
TRENDRADAR_API_PORT=8080 \
TRENDRADAR_PROJECT_ROOT=/app \
RUN_MODE=api
```

## Token

生产环境建议生成随机 token：

```bash
openssl rand -hex 32
```

TrendRadar 和 `cus_bot_ui` 两边必须使用同一个 token。

请求 `/api/*` 时需要：

```http
Authorization: Bearer <TRENDRADAR_API_TOKEN>
```

`GET /health` 不需要鉴权。

如果 `TRENDRADAR_API_TOKEN` 为空，服务会允许访问 `/api/*`，仅建议本地开发使用，启动时会打印高风险警告。

## Zeabur 配置

TrendRadar 一键部署服务建议配置：

```env
RUN_MODE=api
TRENDRADAR_API_PORT=8080
TRENDRADAR_API_TOKEN=<token>
TRENDRADAR_PROJECT_ROOT=/app
```

如果希望这个服务继续按 `CRON_SCHEDULE` 定时爬取，同时让 REST API 占用 Zeabur 暴露的 `8080` 端口，可以使用：

```env
RUN_MODE=cron
TRENDRADAR_API_PORT=8080
TRENDRADAR_API_TOKEN=<token>
TRENDRADAR_PROJECT_ROOT=/app
```

这种模式下，入口脚本会跳过旧的静态 Web 服务器，避免和 REST API 争抢 `8080` 端口。

如果 Zeabur 服务名为 `trendradar`，`cus_bot_ui` 建议使用内网地址：

```env
TRENDRADAR_ENABLED=true
TRENDRADAR_API_BASE_URL=http://trendradar.zeabur.internal:8080
TRENDRADAR_API_TOKEN=<same-token-as-trendradar>
```

公网域名测试时通常不需要写容器端口：

```env
TRENDRADAR_API_BASE_URL=https://news.cuhp.space
```

## 接口列表

### GET /health

健康检查，不需要鉴权。

响应：

```json
{
  "ok": true,
  "service": "trendradar-api",
  "version": "6.10.0",
  "generatedAt": "2026-06-21T07:21:46.226Z"
}
```

### GET /api/news/latest

获取最新热榜新闻。

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `platforms` | 空 | 逗号分隔平台 ID，不传则全部 |
| `limit` | `50` | 返回数量，上限 `200` |
| `includeUrl` | `true` | 是否返回原文 URL |

示例：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/news/latest?limit=3&includeUrl=true"
```

响应结构：

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "id": "wallstreetcn-hot:0ddf2f527e0a34ea",
        "title": "新闻标题",
        "platformId": "wallstreetcn-hot",
        "platformName": "华尔街见闻",
        "rank": 1,
        "summary": null,
        "publishedAt": null,
        "collectedAt": "2026-04-21T14:40:32.000Z",
        "weight": 76.0,
        "url": "https://example.com/news"
      }
    ],
    "generatedAt": "2026-06-21T07:21:46.392Z"
  }
}
```

### GET /api/rss/latest

获取最新 RSS。

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `feeds` | 空 | 逗号分隔 RSS feed ID，不传则全部 |
| `days` | `1` | 最近 N 天，上限 `30` |
| `limit` | `50` | 返回数量，上限 `200` |
| `includeSummary` | `false` | 是否返回摘要 |

示例：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/rss/latest?limit=3"
```

响应结构：

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "id": "36kr:d6fa45c8d19bb9a0",
        "title": "RSS 标题",
        "feedId": "36kr",
        "feedName": "36氪",
        "url": "https://example.com/article",
        "summary": null,
        "publishedAt": "2026-04-21T06:40:16.000Z",
        "collectedAt": "2026-04-21T14:41:05.000Z"
      }
    ],
    "generatedAt": "2026-06-21T07:21:46.407Z"
  }
}
```

### GET /api/news/search

搜索热榜新闻，可同时搜索 RSS。

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `q` | 无 | 搜索关键词，必填 |
| `days` | `7` | 最近 N 天，上限 `30` |
| `platforms` | 空 | 逗号分隔平台 ID |
| `feeds` | 空 | 逗号分隔 RSS feed ID |
| `includeRss` | `true` | 是否同时搜索 RSS |
| `limit` | `50` | 热榜结果数量，上限 `200` |
| `rssLimit` | `20` | RSS 结果数量，上限 `200` |

示例：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/news/search?q=AI&limit=3&rssLimit=3&includeRss=true"
```

响应结构：

```json
{
  "ok": true,
  "data": {
    "query": "AI",
    "news": [],
    "rss": [],
    "generatedAt": "2026-06-21T07:21:46.407Z"
  }
}
```

说明：`limit` 只限制热榜结果，RSS 结果由 `rssLimit` 控制。

### GET /api/topics/trending

获取热点话题。

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `topN` | `10` | 返回数量，上限 `200` |
| `mode` | `current` | `current` 或 `daily` |
| `extractMode` | `keywords` | `keywords` 或 `auto_extract` |

示例：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/topics/trending?topN=5"
```

响应结构：

```json
{
  "ok": true,
  "data": {
    "topics": [
      {
        "keyword": "AI 相关",
        "count": 3,
        "weight": 60.0,
        "platforms": ["thepaper", "wallstreetcn-hot"]
      }
    ],
    "generatedAt": "2026-06-21T07:21:46.378Z"
  }
}
```

### GET /api/reports/summary

获取摘要报告。

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `type` | `daily` | `daily` 或 `weekly` |
| `start` | 空 | 开始日期，`YYYY-MM-DD` |
| `end` | 空 | 结束日期，`YYYY-MM-DD` |

示例：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/reports/summary?type=daily"
```

响应结构：

```json
{
  "ok": true,
  "data": {
    "type": "daily",
    "markdown": "# 每日新闻热点摘要\n...",
    "generatedAt": "2026-06-21T07:21:46.407Z"
  }
}
```

### POST /api/crawl/trigger

手动触发爬取。

请求体：

```json
{
  "platforms": ["wallstreetcn-hot", "cls-hot"],
  "saveToLocal": true,
  "includeUrl": true
}
```

示例：

```bash
curl -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"platforms":["wallstreetcn-hot","cls-hot"],"saveToLocal":true,"includeUrl":true}' \
  "http://trendradar.zeabur.internal:8080/api/crawl/trigger"
```

响应结构：

```json
{
  "ok": true,
  "data": {
    "message": "Crawl finished",
    "successPlatforms": ["wallstreetcn-hot"],
    "failedPlatforms": [],
    "generatedAt": "2026-06-21T07:21:46.407Z"
  }
}
```

该接口有简单限频，连续触发过快会返回 `429`。

## 错误格式

```json
{
  "ok": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Unauthorized"
  }
}
```

常见状态码：

| 状态码 | 场景 |
| --- | --- |
| `400` | 参数错误 |
| `401` | token 缺失或错误 |
| `404` | 路径不存在 |
| `429` | 手动爬取触发过于频繁 |
| `500` | 内部错误 |
| `503` | 数据暂不可用 |

## 验收命令

内网地址按实际 Zeabur 服务名调整。

```bash
curl http://trendradar.zeabur.internal:8080/health
```

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/news/latest?limit=3&includeUrl=true"
```

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/rss/latest?limit=3"
```

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/topics/trending?topN=5"
```

```bash
curl -H "Authorization: Bearer <token>" \
  "http://trendradar.zeabur.internal:8080/api/news/search?q=AI&limit=3&rssLimit=3&includeRss=true"
```

## 实现说明

- REST API 实现在 `trendradar/api.py`。
- 命令入口在 `pyproject.toml` 中注册为 `trendradar-api`。
- API 使用 Python 标准库 HTTP 服务实现，没有新增 Web 框架依赖。
- `latest` 接口读取本地磁盘中最新可用日期的数据，不强制绑定系统当天日期。

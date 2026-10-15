# 币安华语 @binancezh 监控 → 个人微信推送

监控 `https://x.com/binancezh` 的更新，一旦发新推文，立刻推到你**个人微信**。
推送走 **Server酱**（扫码关注「方糖」服务号拿 SendKey，直推个人微信；免费 5 条/天，会员 8 元/月=1000 条/天）。

> 注：2026 年微信已封杀多数第三方推送通道。WxPusher 公众号通道已失效（只能装它 App 收）、
> 企业微信「微信插件」收不到群机器人消息、企业微信自建应用需备案域名+可信IP（个人被卡死）。
> 目前**仍进个人微信的免费通道只剩 Server酱**；其余备选见配置项说明。

**当前部署在 GitHub Actions**（墙外免费服务器，每 5 分钟自动检查）——家云在墙内连不上 X 已弃用，
Oracle 免费服务器因无可用验证卡暂缓。部署方法见 **[GitHubActions部署指南.md](GitHubActions部署指南.md)**。

---

## 一、整体架构

```
x.com/binancezh
      │  (syndication：Twitter 官方 widget 接口，免登录、免 cookie)
      ▼
  monitor.py（GitHub Actions，每 5 分钟）──►  Server酱  ──►  你的个人微信（方糖服务号）
```

> 为什么用 syndication？X 官方读时间线 API 已收费（$200/月）；RSSHub 需要 Node 18+。
> syndication 是 X 官方给嵌入式 widget 用的时间线接口，**无需登录态**即可拿到账号最近推文
> （含 ID/全文/时间），已实测稳定解析 99 条。twscrape（登录态抓取）保留为备选源。

---

## 二、你需要提供的两个凭证（都在 config.env 里填）

### 1) 推送（当前在用：Server酱）
- **Server酱**（推荐，进个人微信）：打开 https://sct.ftqq.com → 微信扫码登录（自动关注「方糖」服务号）→ 复制 **SendKey**（形如 `SCTxxxx`）填 `SERVERCHAN_KEY`，`PUSH_MODE=serverchan`。免费 5 条/天；会员 8 元/月=1000 条/天。
- **备选**：PushPlus（https://pushplus.plus，token，3.9 元/200 条天）/ WxPusher（公众号通道 2026 已被微信封，仅能装 App 收）。改 `PUSH_MODE` 与对应 key 即可，代码均已支持。

### 2) X 登录 Cookie（仅 twscrape 备选源需要）
当前 syndication 源**不需要任何 cookie**。若想启用 twscrape 备选（SOURCE_MODE=auto 时降级用）：
浏览器登录 x.com → F12 → Application → Cookies，复制这几个值拼成一行填 `TW_COOKIE`：
```
auth_token=xxxx; ct0=yyyy; twid=u%3Azzz
```

---

## 三、部署

**当前：GitHub Actions（墙外，免服务器免卡）** → 见 [GitHubActions部署指南.md](GitHubActions部署指南.md)，6 步约 5 分钟。

**已弃用：家云** —— 实测家云（192.168.1.3）在墙内，`x.com`/`api.x.com`/`syndication.twitter.com`/
`cdn.syndication.twimg.com` 全被 GFW 封（TCP 超时/SSL 错误），twscrape 报 ConnectError 即此因，
非代码问题。systemd 服务已停。

**暂缓：Oracle 免费服务器** —— 差一张能做境外预授权的 Visa/Mastercard（livi 卡 2024-09-26 起已停用；
蚂蚁银行无卡产品）。以后有卡了可随时平移，代码不用改。

---

## 四、配置项（config.env 速查）

| 变量 | 默认 | 说明 |
|------|------|------|
| `PUSH_MODE` | `serverchan` | `wxpusher` / `serverchan` / `pushplus` / `wecom_webhook` / `wecom_app` |
| `WXPUSHER_TOKEN` | 空 | WxPusher APP_TOKEN（推送用） |
| `WXPUSHER_UID` | 空 | WxPusher 接收者 UID，多个逗号分隔 |
| `SERVERCHAN_KEY` | 空 | Server酱 SendKey（备选推送用） |
| `PUSHPLUS_TOKEN` | 空 | PushPlus token（备选推送用） |
| `SOURCE_MODE` | `syndication`（Actions 部署用） | `syndication`（Twitter widget 接口，免登录，推荐）/ `twscrape`（登录态抓取）/ `rss`（任意 RSS 源）/ `auto`（syndication→twscrape→rss 自动降级） |
| `TW_SCREEN_NAME` | `binancezh` | 要监控的 X 账号 |
| `TW_COOKIE` | 空 | X 登录 cookie（仅 twscrape 源需要） |
| `TW_LIMIT` | `20` | 每次轮询取多少条 |
| `RSS_URL` | `http://localhost:1200/twitter/user/binancezh` | 仅 `SOURCE_MODE=rss` 时用 |
| `FETCH_PROXY_URL` | 空 | 抓取代理地址（强烈推荐用于 GitHub Actions）。GitHub 的云 IP 会被 Twitter 限流(429)，设此值后抓取改走该代理（典型是 Cloudflare Worker，免费无需信用卡）。见 `CloudflareWorker部署.md` |
| `POLL_INTERVAL` | `300` | 轮询间隔（秒），建议 ≥120 |
| `KEYWORD_FILTER` | 空 | 关键词过滤，逗号分隔；空=全部 |
| `MAX_FAILS_BEFORE_ALERT` | `5` | 连续失败几次后发微信告警 |
| `HTTP_TIMEOUT` | `20` | HTTP 超时（秒） |

> 企业微信两种模式（`wecom_webhook` / `wecom_app`）与 Server酱/PushPlus/WxPusher 仍保留兼容，但本方案默认走 **Server酱** 直推个人微信（2026 年仅剩的免费个人微信通道）。WxPusher 公众号通道已被微信封，仅其 App 可用。

---

## 五、常见问题

| 现象 | 处理 |
|------|------|
| 微信收不到 | 检查 `SERVERCHAN_KEY` 是否填对、是否取消关注/屏蔽了「方糖」服务号；先跑 `--test-push` 确认能收到 |
| 抓不到推文 / Actions 里报错 `syndication 429 限流` | **正常现象**：GitHub 云 IP 被 Twitter 限流。代码已内置兜底链（直连 → `FETCH_PROXY_URL` 代理 → jina 兜底）。最稳解：按 `CloudflareWorker部署.md` 部署一个 Worker 并填 `FETCH_PROXY_URL` Secret |
| 想立刻验证真实推送 | 见部署指南「想立刻验证真实推文推送」一节（删掉最新一条 ID 再跑） |
| 首跑不推送 | 正常：首次只记录历史，等下一条新推文才触发 |
| 想只收特定内容 | `KEYWORD_FILTER=上线,公告,维护`（逗号分隔） |

---

## 六、文件清单

```
binancezh-monitor/
├── monitor.py                 # 主程序（syndication/twscrape/rss 三源 + 五模式推送 + 去重 + 告警）
├── config.env                 # 本地配置（本地测试用；GitHub Actions 用 Secrets，不读此文件）
├── requirements.txt           # Python 依赖（requests / feedparser / python-dotenv）
├── .github/workflows/monitor.yml  # GitHub Actions 工作流（每 5 分钟）
├── GitHubActions部署指南.md    # 6 步部署教程
├── CloudflareWorker部署.md     # 可选：Cloudflare Worker 代理（解决 GitHub 429 限流）
├── worker.js                   # Cloudflare Worker 源码（部署到 CF 即得到 FETCH_PROXY_URL）
└── README.md
```

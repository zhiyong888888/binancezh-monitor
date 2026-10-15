# 可选增强：用 Cloudflare Worker 给抓取"换 IP"（解决 GitHub 429 限流）

## 为什么要这步
GitHub Actions 的云服务器 IP 被 Twitter **限流（HTTP 429）**，所以 `check` 运行会报
`syndication 429 限流` 而失败。代码已内置多重兜底（直连 → 代理 → jina），
但最稳的办法是让抓取请求走一个**不受 Twitter 限流的 IP**——典型就是 Cloudflare Worker。

- Cloudflare 免费、**只需邮箱、无需信用卡**
- Worker 跑在 Cloudflare 全球边缘节点（IP 不被 Twitter 针对数据中心限流）
- 它只做一件事：代抓 syndication 接口，把 HTML 回传给 GitHub Actions

> 如果你不想注册 Cloudflare，也可以**先直接重跑 `check`**：代码会自动尝试 jina 兜底，
> 若 jina 在 GitHub 上能通就直接能用。若仍失败，再回来走这一步。

## 部署步骤（约 5 分钟）

1. 打开 https://workers.cloudflare.com/ ，用邮箱**注册/登录**（免费，无卡）
2. 左侧 **Workers 和 Pages** → **创建** → 选 **Worker** → 名称填 `binancezh-fetch` → 创建
3. 进入 Worker 后点 **编辑代码（Quick Edit / 代码）**，把编辑器里原有内容**全部删掉**，
   粘贴本目录 `worker.js` 的全部内容，点 **部署（Deploy）**
4. 部署成功后，页面顶部会显示你的地址，形如：
   `https://binancezh-fetch.<你的子域>.workers.dev`
   复制这个地址
5. 回到 GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**
   - Name：`FETCH_PROXY_URL`
   - Secret：刚才复制的 Worker 地址（含 `https://` 完整 URL）
   - 点 **Add secret**
6. 回到 **Actions** → 左侧 `binancezh-monitor` → **Run workflow** → mode 选 `check` → 运行

若运行变绿✓ 且微信在 @binancezh 发新推文后收到原文，即大功告成。

## 排查
- 仍报 429：说明 Cloudflare 的个别 IP 也被限流，可多次重新 Deploy（换出口 IP），
  或在 `worker.js` 里给 fetch 加 `cf: { resolveOverride: "..." }` 切换数据中心。
- Worker 报错：在 Cloudflare 控制台 **Workers → 日志/观察** 看运行日志。

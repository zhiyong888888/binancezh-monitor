// Cloudflare Worker：替 GitHub Actions 抓取 @binancezh 的 Twitter 时间线
// 作用：GitHub 的云 IP 会被 Twitter 限流(429)，本 Worker 跑在 Cloudflare 边缘节点
//      （IP 不受 Twitter 针对数据中心的限流影响），代抓 syndication 接口后把原始
//      HTML 原样回传。monitor.py 收到 HTML 后照常解析 __NEXT_DATA__。
//
// 部署后把 Worker 地址（形如 https://binancezh-fetch.<子域>.workers.dev ）填到
// GitHub 仓库 Secrets 的 FETCH_PROXY_URL 即可。
//
// 可选鉴权：在 Worker 设置 → Variables → 添加环境变量 SECRET（任意随机串），
// 则调用方需以 https://<worker>/?token=<SECRET> 访问，避免被公开滥用导致 X 限流。
// 不设 SECRET 则公开（仅抓取公开数据，风险可控）。

export default {
  async fetch(request, env, ctx) {
    // —— 可选鉴权 ——
    const SECRET = env.SECRET || "";
    if (SECRET) {
      const url = new URL(request.url);
      if (url.searchParams.get("token") !== SECRET) {
        return new Response("forbidden", { status: 403 });
      }
    }

    const target =
      "https://syndication.twitter.com/srv/timeline-profile/screen-name/binancezh" +
      "?dnt=true&embedId=twitter-widget-0";

    const headers = {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9",
      "Referer": "https://platform.twitter.com/",
    };

    // 上游偶发抖动时重试，提升稳定性
    let lastErr = null;
    for (let i = 0; i < 3; i++) {
      try {
        const resp = await fetch(target, { headers });
        const text = await resp.text();
        if (resp.status === 429) {
          throw new Error("upstream 429");
        }
        return new Response(text, {
          status: resp.status,
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
          },
        });
      } catch (e) {
        lastErr = e;
        await new Promise((r) => setTimeout(r, 1500 * (i + 1)));
      }
    }
    return new Response("upstream fetch failed: " + String(lastErr), {
      status: 502,
    });
  },
};

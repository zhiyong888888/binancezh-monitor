// Cloudflare Worker：替 GitHub Actions 抓取 @binancezh 的 Twitter 时间线
// 作用：GitHub 的云 IP 会被 Twitter 限流(429)，本 Worker 跑在 Cloudflare 边缘节点
//      （IP 不受 Twitter 针对数据中心的限流影响），代抓 syndication 接口后把原始
//      HTML 原样回传。monitor.py 收到 HTML 后照常解析 __NEXT_DATA__。
//
// 部署后把 Worker 地址（形如 https://binancezh-fetch.<子域>.workers.dev ）填到
// GitHub 仓库 Secrets 的 FETCH_PROXY_URL 即可。

export default {
  async fetch(request, env, ctx) {
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

    const resp = await fetch(target, { headers });
    const text = await resp.text();

    return new Response(text, {
      status: resp.status,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
      },
    });
  },
};

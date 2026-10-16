// Cloudflare Worker：替 GitHub Actions 抓取 @binancezh 的 Twitter 时间线
// 作用：GitHub 的云 IP 会被 Twitter 限流(429)，本 Worker 跑在 Cloudflare 边缘节点
//      代抓 syndication 接口后把原始 HTML 原样回传。monitor.py 收到 HTML 后照常
//      解析 __NEXT_DATA__。
//
// v2 关键改进：内存缓存（3 分钟 TTL）
//   - 无论多少请求并发/连发，3 分钟窗口内上游 syndication 最多被打 1 次
//   - 避免 Preview 连点、cron 高频触发把上游配额打爆（否则必 429）
//   - 上游失败时若有旧缓存，回退返回旧缓存（比报 502 好）
//
// 部署后把 Worker 地址（形如 https://binancezh-fetch.<子域>.workers.dev ）填到
// GitHub 仓库 Secrets 的 FETCH_PROXY_URL 即可。
//
// 可选鉴权：在 Worker 设置 → Variables → 添加环境变量 SECRET（任意随机串），
// 则调用方需以 https://<worker>/?token=<SECRET> 访问，避免被公开滥用导致 X 限流。
// 不设 SECRET 则公开（仅抓取公开数据，风险可控）。

// —— 模块级内存缓存（同一 isolate 内共享；isolate 生命周期内有效） ——
const CACHE_TTL_MS = 3 * 60 * 1000; // 3 分钟
let cachedResp = null; // { ts: number, body: string }

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

    // —— 缓存命中：直接回，不打上游 ——
    const now = Date.now();
    if (cachedResp && now - cachedResp.ts < CACHE_TTL_MS) {
      return new Response(cachedResp.body, {
        status: 200,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-store",
          "X-Cache": "HIT",
        },
      });
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

    // 上游偶发抖动时重试；退避拉长（2s/5s/8s），给限流窗口留恢复时间
    let lastErr = null;
    for (let i = 0; i < 4; i++) {
      try {
        const resp = await fetch(target, { headers });
        const text = await resp.text();
        if (resp.status === 429) {
          throw new Error("upstream 429");
        }
        if (!resp.ok) {
          throw new Error("upstream " + resp.status);
        }
        // 成功：写入缓存再返回
        cachedResp = { ts: Date.now(), body: text };
        return new Response(text, {
          status: 200,
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
            "X-Cache": "MISS",
          },
        });
      } catch (e) {
        lastErr = e;
        if (i < 3) {
          await new Promise((r) => setTimeout(r, 2000 + 3000 * i));
        }
      }
    }

    // —— 全部重试失败：有旧缓存就回旧缓存（总比 502 好）——
    if (cachedResp) {
      return new Response(cachedResp.body, {
        status: 200,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-store",
          "X-Cache": "STALE",
        },
      });
    }
    return new Response("upstream fetch failed: " + String(lastErr), {
      status: 502,
    });
  },
};

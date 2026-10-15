#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安华语 (@binancezh) 监控 + 推送

监控源 (SOURCE_MODE):
  syndication : Twitter 官方 widget syndication 接口抓时间线（免登录、免 cookie，推荐）
            注意：该域名在墙内部分 IP 被 GFW 封（家云不可达），
            适用于 GitHub Actions 等墙外环境；本机宽带多数可达
  rss      : 任意 RSS / Atom feed（如自托管 RSSHub 的 /twitter/user/binancezh）
  twscrape : 用登录态 X 账号直接抓时间线（纯 Python，免 RSSHub / 免 Node）
            需要 TW_COOKIE（浏览器登录 x.com 后复制的 auth_token cookie）
            家云在墙内直连 X 被封时，还需 TWS_PROXY（socks5/http 代理，走梯子出 X）
  auto     : 自动链：syndication → twscrape（有 cookie 时）→ rss

推送端 (PUSH_MODE):
  wxpusher      : WxPusher（关注公众号「wxpusher」拿 UID，免费、直推个人微信，当前默认）
  serverchan    : Server酱（微信扫码拿 SendKey，直推个人微信）
  pushplus      : PushPlus（微信扫码拿 token，直推个人微信）
  wecom_webhook : 企业微信群机器人（仅企业微信 App，不进个人微信）
  wecom_app     : 企业微信自建应用 + 微信插件（可进个人微信，但需 IP 白名单+可信域名/备案）

用法:
  python monitor.py            # 常驻循环（配合 systemd / docker）
  python monitor.py --once    # 单次运行（抓取一次后退出，用于调试）
  python monitor.py --test-push   # 发一条测试消息，验证当前推送模式
"""

import os
import re
import sys
import time
import json
import asyncio
import logging
from pathlib import Path

import requests
import feedparser

# 兼容不装 python-dotenv 的情况
try:
    from dotenv import load_dotenv
    load_dotenv()                                              # 兼容 .env
    load_dotenv(Path(__file__).resolve().parent / "config.env")  # 兼容本项目的 config.env（本地测试用）
except Exception:
    pass

# ---------------------------------------------------------------------------
# 配置（环境变量优先，其次 config.env 文件）
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

RSS_URL = os.getenv("RSS_URL", "http://localhost:1200/twitter/user/binancezh")
SOURCE_MODE = os.getenv("SOURCE_MODE", "twscrape")  # rss | twscrape
TW_SCREEN_NAME = os.getenv("TW_SCREEN_NAME", "binancezh")
TW_COOKIE = os.getenv("TW_COOKIE", "")              # 登录态 cookie（auth_token=...; ct0=...; twid=...）
TW_LIMIT = int(os.getenv("TW_LIMIT", "20"))
# 代理（仅 SOURCE_MODE=twscrape 且家云在墙内直连 X 被封时需要）
# 格式：socks5://user:pass@host:port 或 http://host:port
# 注意：必须是家云（192.168.1.3）能访问到的地址，不能是 127.0.0.1（那是家云自己）
TWS_PROXY = os.getenv("TWS_PROXY", "")
if TWS_PROXY:
    # twscrape 通过环境变量 TWS_PROXY 把请求走代理，需在 import twscrape 前设好
    os.environ["TWS_PROXY"] = TWS_PROXY.strip()

# 抓取代理 URL（可选，强烈推荐用于 GitHub Actions）。
# 用途：GitHub 的云 IP 会被 Twitter 限流（429），因此让抓取请求改走一个
# 运行在「不受限 IP」上的代理（典型是 Cloudflare Worker，免费、无需信用卡），
# 由它去抓 Twitter 再把原始 HTML 回传。
# 例如：https://binancezh-fetch.<你的子域>.workers.dev
# 不填则直接抓（在你本机/有代理的环境可用，GitHub 上会 429）。
FETCH_PROXY_URL = os.getenv("FETCH_PROXY_URL", "").strip()

PUSH_MODE = os.getenv("PUSH_MODE", "serverchan")    # serverchan | pushplus | wxpusher | wecom_webhook | wecom_app
SERVERCHAN_KEY = os.getenv("SERVERCHAN_KEY", "")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "")
WXPUSHER_TOKEN = os.getenv("WXPUSHER_TOKEN", "")
WXPUSHER_UID = os.getenv("WXPUSHER_UID", "")         # 多个 UID 用逗号分隔

# 企业微信（wecom_webhook / wecom_app 模式用）
WECOM_WEBHOOK = os.getenv("WECOM_WEBHOOK", "")
WECOM_CORPID = os.getenv("WECOM_CORPID", "")
WECOM_AGENTID = os.getenv("WECOM_AGENTID", "")
WECOM_CORPSECRET = os.getenv("WECOM_CORPSECRET", "")
WECOM_TOUSER = os.getenv("WECOM_TOUSER", "@all")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
KEYWORD_FILTER = [k.strip() for k in os.getenv("KEYWORD_FILTER", "").split(",") if k.strip()]
STATE_FILE = os.getenv("STATE_FILE", str(BASE_DIR / ".last_seen.json"))
LOG_FILE = os.getenv("LOG_FILE", str(BASE_DIR / "monitor.log"))
MAX_FAILS_BEFORE_ALERT = int(os.getenv("MAX_FAILS_BEFORE_ALERT", "5"))
SEEN_KEEP = int(os.getenv("SEEN_KEEP", "500"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("binancezh-monitor")


# ---------------------------------------------------------------------------
# 状态持久化（记录已推送的条目，避免重复 / 重启丢失）
# ---------------------------------------------------------------------------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": [], "first_run": True, "alerted_outage": False}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# 抓取与解析
# ---------------------------------------------------------------------------
def fetch_rss():
    resp = requests.get(RSS_URL, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _parse_cookie(raw):
    """把 'a=1; b=2' 形式的 cookie 字符串解析成 dict。"""
    out = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _collect_tweet_objects(obj, depth=0):
    """递归收集 syndication __NEXT_DATA__ 里的推文对象（含 full_text + id_str）。"""
    if depth > 9:
        return []
    out = []
    if isinstance(obj, dict):
        if "full_text" in obj and "id_str" in obj:
            out.append(obj)
        for v in obj.values():
            out.extend(_collect_tweet_objects(v, depth + 1))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_collect_tweet_objects(v, depth + 1))
    return out


def _parse_syndication_html(html):
    """从 syndication 返回的 HTML 里提取 @TW_SCREEN_NAME 的推文 entry 列表（旧→新）。"""
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S,
    )
    if not m:
        raise RuntimeError("页面无 __NEXT_DATA__（接口结构可能已变）")
    data = json.loads(m.group(1))
    own, seen = [], set()
    for t in _collect_tweet_objects(data):
        tid = str(t.get("id_str") or "")
        user = (t.get("user") or {}).get("screen_name", "") or ""
        if not tid or tid in seen:
            continue
        if user.lower() != TW_SCREEN_NAME.lower():
            continue  # 过滤引用推/转发里夹杂的他人推文
        seen.add(tid)
        own.append(t)
    if not own:
        raise RuntimeError("未解析到任何推文（可能被限流或结构变更）")
    entries = []
    for t in own:
        tid = str(t["id_str"])
        entries.append({
            "id": tid,
            "title": t.get("full_text") or "",
            "link": f"https://x.com/{TW_SCREEN_NAME}/status/{tid}",
            "published": t.get("created_at") or "",
        })
    # 推文 ID 为雪花 ID，随时间单调递增，按 ID 排序即时间序（旧→新）
    entries.sort(key=lambda e: int(e["id"]))
    return entries


def fetch_syndication(proxy_url=None):
    """Twitter 官方 widget syndication 接口抓 @TW_SCREEN_NAME 最近推文（免登录、免 cookie）。
    - proxy_url 为空：直接抓（本机/有代理环境可用；GitHub 云 IP 会被 429 限流）
    - proxy_url 非空：改抓该代理（典型是 Cloudflare Worker，跑在不受限 IP 上替我们抓 X）
    返回按时间排序（旧→新）的 entry 列表；内置 429 限流重试。
    """
    if proxy_url:
        url = proxy_url
        params = None
        headers = {"User-Agent": USER_AGENT}
        log.info("经代理 %s 抓取 syndication", proxy_url)
    else:
        url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{TW_SCREEN_NAME}"
        params = {"dnt": "true", "embedId": "twitter-widget-0"}
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://platform.twitter.com/",
        }
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 429:
                raise RuntimeError("syndication 429 限流")
            resp.raise_for_status()
            entries = _parse_syndication_html(resp.text)
            log.info("syndication 抓到 %d 条推文", len(entries))
            return entries
        except Exception as e:
            last_err = e
            log.warning("syndication 第 %d 次尝试失败: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"syndication 抓取失败: {last_err}")


def fetch_via_jina():
    """兜底：经 r.jina.ai 阅读器代理抓 x.com 个人主页（其服务器代抓，绕开 GitHub 的 429）。
    返回 markdown，从中提取推文 ID + 链接 + 附近文本。文本精度不如 syndication JSON，
    作为 syndication / 代理都失败时的最后手段；至少能拿到 ID 与链接触发通知。
    """
    url = f"https://r.jina.ai/https://x.com/{TW_SCREEN_NAME}"
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            md = resp.text
            ids = re.findall(rf"x\.com/{re.escape(TW_SCREEN_NAME)}/status/(\d+)", md)
            if not ids:
                raise RuntimeError("jina 未返回任何推文链接")
            seen, entries = set(), []
            for tid in sorted(set(ids), key=lambda x: int(x)):
                if tid in seen:
                    continue
                seen.add(tid)
                # 取该推文链接附近的纯文本作为近似正文
                idx = md.find(f"status/{tid}")
                seg = md[max(0, idx - 600):idx + 50]
                seg = re.sub(r"!\[.*?\]\(.*?\)", "", seg)        # 去图片
                seg = re.sub(r"\[([^\]]+)\]\(.*?\)", r"\1", seg)  # 去链接标记
                lines = [l.strip() for l in seg.splitlines() if l.strip()]
                text = lines[-3:] and "\n".join(lines[-3:]) or ""
                text = (text or "（点开查看原文）")[:800]
                entries.append({
                    "id": tid,
                    "title": text,
                    "link": f"https://x.com/{TW_SCREEN_NAME}/status/{tid}",
                    "published": "",
                })
            log.info("jina 抓到 %d 条推文（近似文本）", len(entries))
            return entries
        except Exception as e:
            last_err = e
            log.warning("jina 第 %d 次尝试失败: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"jina 抓取失败: {last_err}")


def fetch_syndication_auto():
    """韧性链：直接抓 → (若配了)代理抓 → jina 兜底。任一成功即返回。"""
    try:
        return fetch_syndication()
    except Exception as e:
        log.warning("syndication 直连失败: %s", e)
    if FETCH_PROXY_URL:
        try:
            return fetch_syndication(proxy_url=FETCH_PROXY_URL)
        except Exception as e:
            log.warning("经代理抓取失败: %s", e)
    try:
        return fetch_via_jina()
    except Exception as e:
        raise RuntimeError(f"所有抓取方式均失败: {e}")


def fetch_twscrape():
    """用 twscrape + 登录态 cookie 抓 @TW_SCREEN_NAME 的最新推文（兼容 0.20.x API）。"""
    if not TW_COOKIE:
        raise RuntimeError("TW_COOKIE 未配置（twscrape 模式需要 X 登录 cookie）")
    try:
        from twscrape import API
    except Exception as e:
        raise RuntimeError("twscrape 未安装，请先 pip install twscrape") from e

    cookie_str = TW_COOKIE.strip()

    async def _run():
        api = API()
        # twscrape 0.20.x：cookie 通过 pool.add_account 的 cookies 参数（原始字符串）注入登录态
        await api.pool.add_account("u", "p", "", "", cookies=cookie_str)
        me = await api.user_by_login(TW_SCREEN_NAME)
        uid = getattr(me, "id", None) or getattr(me, "rest_id", None)
        tweets = []
        async for t in api.user_tweets(uid, limit=TW_LIMIT):
            tweets.append(t)
        return tweets

    tweets = asyncio.run(_run())
    entries = []
    for t in tweets:
        tid = str(getattr(t, "id", ""))
        text = getattr(t, "rawContent", None) or getattr(t, "text", "") or ""
        link = f"https://x.com/{TW_SCREEN_NAME}/status/{tid}"
        published = ""
        ca = getattr(t, "date", None) or getattr(t, "createdAt", None)
        if ca is not None:
            try:
                published = ca.isoformat()
            except Exception:
                published = str(ca)
        entries.append({"id": tid, "title": text, "link": link, "published": published})
    return entries


def fetch_source():
    """按 SOURCE_MODE 抓取，返回 entry 列表（dict: id/title/link/published）。"""
    if SOURCE_MODE == "syndication":
        return fetch_syndication_auto()
    if SOURCE_MODE == "auto":
        # 自动链：syndication（免登录）→ 代理（若配）→ jina 兜底 → twscrape（需 cookie）→ rss
        try:
            return fetch_syndication_auto()
        except Exception as e:
            log.warning("syndication 全链失败，降级: %s", e)
            if TW_COOKIE:
                try:
                    return fetch_twscrape()
                except Exception as e2:
                    log.warning("twscrape 也失败，再降级 RSS: %s", e2)
            else:
                log.warning("未配置 TW_COOKIE，跳过 twscrape，降级 RSS")
    elif SOURCE_MODE == "twscrape":
        return fetch_twscrape()
    # 默认 rss
    feed = fetch_rss()
    return [
        {
            "id": e.get("id") or e.get("link") or e.get("guid"),
            "title": (e.get("title") or "").strip() or (e.get("summary") or "").strip(),
            "link": e.get("link") or "",
            "published": e.get("published") or e.get("updated") or "",
        }
        for e in feed.entries
    ]


# ---------------------------------------------------------------------------
# 推送（四种模式）
# ---------------------------------------------------------------------------
def send_serverchan(title, content, short=None):
    if not SERVERCHAN_KEY:
        log.warning("未配置 SERVERCHAN_KEY，跳过推送")
        return False
    key = SERVERCHAN_KEY.strip()
    if key.startswith("SCT"):
        url = f"https://sctapi.ftqq.com/{key}.send"
    else:
        url = f"https://sc.ftqq.com/{key}.send"
    data = {"title": title, "desp": content}
    # short 是微信消息卡片的摘要（上限 64 字符，直接展示，不用点开）
    if short:
        data["short"] = short
    try:
        r = requests.post(url, data=data, timeout=HTTP_TIMEOUT)
        data = r.json()
        if data.get("code", data.get("errcode", 0)) not in (0, 200):
            log.error("Server酱推送失败: %s", data)
            return False
        return True
    except Exception as ex:
        log.error("Server酱推送异常: %s", ex)
        return False


def send_pushplus(title, content):
    if not PUSHPLUS_TOKEN:
        log.warning("未配置 PUSHPLUS_TOKEN，跳过推送")
        return False
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": PUSHPLUS_TOKEN.strip(),
        "title": title,
        "content": content.replace("\n", "<br>"),
        "template": "html",
    }
    try:
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        data = r.json()
        if data.get("code", 0) != 200:
            log.error("PushPlus 推送失败: %s", data)
            return False
        return True
    except Exception as ex:
        log.error("PushPlus 推送异常: %s", ex)
        return False


def send_wxpusher(title, content):
    if not WXPUSHER_TOKEN:
        log.warning("未配置 WXPUSHER_TOKEN，跳过推送")
        return False
    uids = [u.strip() for u in WXPUSHER_UID.split(",") if u.strip()]
    if not uids:
        log.warning("未配置 WXPUSHER_UID，跳过推送")
        return False
    url = "https://wxpusher.zjiecode.com/api/send/message"
    payload = {
        "appToken": WXPUSHER_TOKEN.strip(),
        "content": f"{title}\n\n{content}",
        "summary": title[:100],
        "contentType": 1,  # 1=文本
        "uids": uids,
    }
    try:
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        data = r.json()
        if data.get("success") is not True and data.get("code", 0) != 1000:
            log.error("WxPusher 推送失败: %s", data)
            return False
        if not data.get("success"):
            log.error("WxPusher 推送失败: %s", data)
            return False
        return True
    except Exception as ex:
        log.error("WxPusher 推送异常: %s", ex)
        return False


def _get_wecom_token():
    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    r = requests.get(url, params={"corpid": WECOM_CORPID, "corpsecret": WECOM_CORPSECRET}, timeout=HTTP_TIMEOUT)
    data = r.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"获取 access_token 失败: {data}")
    return data["access_token"]


def send_wecom_webhook(body):
    if not WECOM_WEBHOOK:
        log.warning("未配置 WECOM_WEBHOOK，跳过推送")
        return False
    payload = {"msgtype": "markdown", "markdown": {"content": body}}
    try:
        r = requests.post(WECOM_WEBHOOK, json=payload, timeout=HTTP_TIMEOUT)
        data = r.json()
        if data.get("errcode", 0) != 0:
            log.error("企业微信推送失败: %s", data)
            return False
        return True
    except Exception as ex:
        log.error("企业微信推送异常: %s", ex)
        return False


def send_wecom_app(body):
    if not (WECOM_CORPID and WECOM_AGENTID and WECOM_CORPSECRET):
        log.warning("未配置 WECOM 自建应用三要素，跳过推送")
        return False
    try:
        token = _get_wecom_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {"msgtype": "text", "agentid": int(WECOM_AGENTID), "touser": WECOM_TOUSER or "@all", "text": {"content": body}}
        r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        data = r.json()
        if data.get("errcode", 0) != 0:
            log.error("企业微信应用推送失败: %s", data)
            return False
        return True
    except Exception as ex:
        log.error("企业微信应用推送异常: %s", ex)
        return False


def build_messages(entries):
    """把 entries 转成 (title, text, short) 列表。

    Server酱 微信消息卡片直接展示的是 title（≤32 字符）和 short 摘要（≤64 字符），
    desp 正文要点开才看。为做到"不点开也能看到推文原文"，把原文放进 title/short，
    完整原文 + 链接 + 时间放 desp。
    """
    out = []
    for e in entries:
        text = (e.get("title") or "").strip()
        link = e.get("link") or ""
        t = e.get("published") or ""
        # 标题直接放推文原文（Server酱 title 上限 32 字符，超出服务端自动截断）
        # 压成单行，避免卡片标题里出现换行导致显示异常
        flat = " ".join(text.split())
        push_title = (flat[:32] or f"@{TW_SCREEN_NAME} 更新").strip()
        # 卡片摘要 short：原文前 64 字（不传则 Server酱 自动从 desp 截取，效果不可控）
        push_short = (flat[:64] or push_title).strip()
        lines = [text[:800], f"查看原推: {link}"]
        if t:
            lines.append(f"时间: {t}")
        body = "\n".join(lines)
        out.append((push_title, body, push_short))
    return out


def notify_entries(entries):
    """按 PUSH_MODE 推送一批 entries，返回成功条数。"""
    msgs = build_messages(entries)
    ok_count = 0
    for title, body, short in msgs:
        if PUSH_MODE == "serverchan":
            ok = send_serverchan(title, body, short=short)
        elif PUSH_MODE == "pushplus":
            ok = send_pushplus(title, body)
        elif PUSH_MODE == "wxpusher":
            ok = send_wxpusher(title, body)
        elif PUSH_MODE == "wecom_app":
            ok = send_wecom_app(body)
        else:  # wecom_webhook
            ok = send_wecom_webhook("## " + title + "\n\n> " + body.replace("\n", "\n> "))
        if ok:
            ok_count += 1
        else:
            break
    return ok_count


def build_alert(fails, ex):
    return (f"⚠️ 监控源异常\n连续 {fails} 次抓取失败：{ex}\n"
            "请检查监控源 / 网络 / X Cookie 是否过期")


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------
def run_once(state):
    """抓取一次源，推送新条目。返回本轮推送条数。"""
    entries = fetch_source()
    if not entries:
        raise RuntimeError("源为空（账号可能无新内容，或监控源异常）")

    seen = set(state.get("seen", []))
    new = [e for e in entries if e.get("id") and e.get("id") not in seen]

    # 首次运行：只记录历史，不推送（避免一上来轰炸历史推文）
    if state.get("first_run"):
        state["seen"] = [e["id"] for e in entries[:SEEN_KEEP] if e.get("id")]
        state["first_run"] = False
        log.info("首次运行，已记录 %d 条历史，不推送", len(state["seen"]))
        return 0

    # 关键词过滤
    notify = []
    for e in new:
        if KEYWORD_FILTER:
            blob = ((e.get("title") or "") + " " + (e.get("link") or "")).lower()
            if not any(k.lower() in blob for k in KEYWORD_FILTER):
                seen.add(e["id"])
                continue
        notify.append(e)

    # 旧的先推，新的后推（与发布时间顺序一致）
    ok = notify_entries(list(reversed(notify)))
    for e in notify[:ok]:
        seen.add(e["id"])
    if ok < len(notify):
        log.warning("推送中断（成功 %d/%d），剩余下次重试", ok, len(notify))

    state["seen"] = list(seen)[-SEEN_KEEP:]
    return ok


def main():
    # 测试推送
    if "--test-push" in sys.argv:
        providers = {
            "serverchan": ("Server酱", lambda: send_serverchan("✅ 推送测试", "Server酱配置成功，监控程序可正常送达个人微信。")),
            "pushplus": ("PushPlus", lambda: send_pushplus("✅ 推送测试", "PushPlus 配置成功，监控程序可正常送达个人微信。")),
            "wxpusher": ("WxPusher", lambda: send_wxpusher("✅ 推送测试", "WxPusher 配置成功，监控程序可正常送达个人微信。")),
            "wecom_app": ("企业微信自建应用", lambda: send_wecom_app("✅ 推送测试\n企业微信自建应用配置成功。")),
            "wecom_webhook": ("企业微信群机器人", lambda: send_wecom_webhook("## ✅ 推送测试\n> 企业微信群机器人配置成功。")),
        }
        name, fn = providers.get(PUSH_MODE, ("未知", lambda: False))
        ok = fn()
        print(f"测试推送({name}):", "成功" if ok else "失败（检查配置）")
        sys.exit(0 if ok else 1)

    state = load_state()
    fails = 0

    # 单次运行（GitHub Actions / cron 场景）
    if "--once" in sys.argv:
        try:
            n = run_once(state)
            state["fails"] = 0
            state["alerted_outage"] = False
            state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
            save_state(state)
            log.info("单次运行完成，本轮推送 %d 条", n)
        except Exception as ex:
            state["fails"] = int(state.get("fails", 0) or 0) + 1
            state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
            log.error("运行出错（连续第 %d 次）: %s", state["fails"], ex)
            # 连续失败达到阈值时，推送告警到微信（用户无需盯 Actions 页面）
            if state["fails"] >= MAX_FAILS_BEFORE_ALERT and not state.get("alerted_outage"):
                try:
                    notify_entries([{"id": "_alert", "title": build_alert(state["fails"], ex),
                                     "link": "", "published": ""}])
                    state["alerted_outage"] = True
                except Exception as ex2:
                    log.error("告警推送失败: %s", ex2)
            try:
                save_state(state)
            except Exception as ex3:
                log.error("状态保存失败: %s", ex3)
            sys.exit(1)
        sys.exit(0)

    # 常驻循环
    log.info("启动监控 | 源=%s | 推送=%s | 间隔=%ss | 关键词=%s",
             SOURCE_MODE, PUSH_MODE, POLL_INTERVAL, KEYWORD_FILTER or "无")
    while True:
        try:
            n = run_once(state)
            save_state(state)
            fails = 0
            state["alerted_outage"] = False
            if n:
                log.info("本轮推送 %d 条新动态", n)
        except Exception as ex:
            fails += 1
            log.error("抓取/处理失败 (%d/%d): %s", fails, MAX_FAILS_BEFORE_ALERT, ex)
            if fails >= MAX_FAILS_BEFORE_ALERT and not state.get("alerted_outage"):
                notify_entries([{"id": "_alert", "title": build_alert(fails, ex), "link": "", "published": ""}])
                state["alerted_outage"] = True
                save_state(state)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

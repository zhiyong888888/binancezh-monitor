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
import random
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
# 单轮最多推送条数（防御：即使去重异常也不会刷屏 / 触发 Server酱 限流）
MAX_PUSH_PER_RUN = int(os.getenv("MAX_PUSH_PER_RUN", "10"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
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
    for attempt in range(2):
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
            if attempt < 4:
                # 随机退避 2~7s，打散请求避免被 429
                time.sleep(2 + attempt * 1.5 + random.uniform(0, 3))
    raise RuntimeError(f"syndication 抓取失败: {last_err}")


def fetch_jina_syndication():
    """云端首选：让 r.jina.ai 以 html 模式抓取 syndication 接口本身。

    为什么不直接抓 x.com 主页：X 已把主页推文链接 blob 化，拿不到 tweet ID。
    而 syndication 接口返回的 HTML 里推文 ID 藏在 __NEXT_DATA__ JSON 中——jina 用
    x-return-format: html 原样回传整段 HTML，__NEXT_DATA__ 完整保留，可解析出全部推文 ID。
    这条对 GitHub 云 IP 同样有效（jina 的出口 IP 未被 X 限流），是替代 Cloudflare Worker 的稳链路。

    缓存击穿（本项目核心漏推根因的修复）：jina 按完整 URL 做缓存。若每次都抓同一个 URL，
    jina 会一直回吐二级缓存里的旧快照，导致最新推文迟迟不出现在结果里（甚至快照回退到更早的
    时间线）。这里拼一个随时间变化的 _cb 参数 + 真实 widget 的 embedId，让 jina 每次都重新
    去 X 拉一次 syndication，拿到最新快照。Twitter 的 syndication 接口会忽略未知 query 参数，
    所以不影响内容，只用于绕过 jina 缓存。
    """
    cb = int(time.time())
    target = (
        f"https://r.jina.ai/https://syndication.twitter.com/srv/timeline-profile/"
        f"screen-name/{TW_SCREEN_NAME}?dnt=true&embedId=twitter-widget-0&_cb={cb}"
    )
    headers = {"User-Agent": USER_AGENT, "x-return-format": "html"}
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(target, headers=headers, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            entries = _parse_syndication_html(resp.text)
            log.info("经 jina(html) 抓到 %d 条推文", len(entries))
            return entries
        except Exception as e:
            last_err = e
            log.warning("jina-syndication 第 %d 次尝试失败: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 + attempt * 2 + random.uniform(0, 2))
    raise RuntimeError(f"jina-syndication 抓取失败: {last_err}")


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
            # 优先匹配普通 status 链接；X 反爬会把部分链接 blob 化导致拿不到 ID，
            # 此时再退一步用「19 位雪花 ID」兜底（推文 ID 多为 19 位纯数字）
            ids = re.findall(rf"x\.com/{re.escape(TW_SCREEN_NAME)}/status/(\d+)", md)
            if not ids:
                ids = re.findall(r"(?<!\d)(\d{18,19})(?!\d)", md)
            if not ids:
                raise RuntimeError("jina 未返回任何推文链接")
            seen, entries = set(), []
            for tid in sorted(set(ids), key=lambda x: int(x)):
                if tid in seen:
                    continue
                seen.add(tid)
                idx = md.find(tid)
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
            if attempt < 4:
                time.sleep(2 + attempt * 1.5 + random.uniform(0, 3))
    raise RuntimeError(f"jina 抓取失败: {last_err}")


def fetch_syndication_auto():
    """韧性链（云端优先）：

    1) jina(html) 抓 syndication 接口 —— GitHub 云 IP 必被 X 直连 429，但 jina 出口未被限流，
       且 html 模式完整保留 __NEXT_DATA__，可解析全部推文 ID（高保真，首选）
    2) 直连 syndication —— 本机/有代理环境可用，GitHub 上几乎必 429
    3) (若配了) Cloudflare Worker 代理 —— 实测 CF IP 也常被 X 限流，作补充
    4) jina 抓 x.com 主页 —— 链接被 blob 化，仅能兜底拿少量 ID（最后手段）
    """
    try:
        return fetch_jina_syndication()
    except Exception as e:
        log.warning("jina-syndication(html) 失败，降级: %s", e)
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
    """抓取一次源，推送新条目。返回本轮推送条数。

    去重以「历史见过的最大雪花 ID（max_id）」为准：只推送 ID 严格大于 max_id 的推文。
    雪花 ID 随时间单调递增，故「比 max_id 大」⇔「比我们见过的最新推文更新」。
    任何历史老推文（即便被 jina 兜底误抓回来）都不可能被当新推文推送。
    """
    entries = fetch_source()
    if not entries:
        raise RuntimeError("源为空（账号可能无新内容，或监控源异常）")

    max_id = int(str(state.get("max_id", "0") or "0"))
    seen = set(state.get("seen", []))

    # 首次运行 / 缺失基线：只记录历史最新 ID，不推送（避免轰炸历史推文或误推老推文）
    if state.get("first_run") or max_id == 0:
        if entries:
            max_id = max(int(e["id"]) for e in entries if e.get("id"))
        state["max_id"] = str(max_id)
        state["seen"] = [e["id"] for e in entries[-SEEN_KEEP:] if e.get("id")]  # 保留最新若干条
        state["first_run"] = False
        log.info("基线初始化，记录最新推文 ID=%s，本轮不推送", max_id)
        return 0

    # 源当前返回的最新 ID
    src_max = max((int(e["id"]) for e in entries if e.get("id")), default=0)
    # 源最新 ID 落后于已记录的 max_id（典型：jina/Twitter 缓存滞后或快照回退），
    # 说明源暂时给不出比已见更新内容。把 max_id 回退到源的最新，
    # 避免「max_id 卡在高位 → 源永远凑不够更新推文 → 永久漏推」。
    # 下方会把源返回的全部 ID 写入 seen，故回退 max_id 不会造成历史推文被重推。
    if src_max and src_max < max_id:
        log.warning("源最新ID(%s)落后于记录max_id(%s)，回退max_id解除漏推卡死", src_max, max_id)
        max_id = src_max

    # 候选：严格比 max_id 新，且未在 seen 中出现过（双保险去重，防回退后重推历史）
    candidates = [e for e in entries if e.get("id") and int(e["id"]) > max_id and e["id"] not in seen]
    candidates.sort(key=lambda e: int(e["id"]))  # 旧→新

    # 关键词过滤
    notify = []
    for e in candidates:
        if KEYWORD_FILTER:
            blob = ((e.get("title") or "") + " " + (e.get("link") or "")).lower()
            if not any(k.lower() in blob for k in KEYWORD_FILTER):
                seen.add(e["id"])
                continue
        notify.append(e)

    # 安全上限：单轮最多推送 MAX_PUSH_PER_RUN 条，避免异常时刷屏 / 触发 Server酱 限流
    notify = notify[:MAX_PUSH_PER_RUN]

    # 旧的先推，新的后推（与发布时间顺序一致）
    ok = notify_entries(list(reversed(notify)))
    pushed = notify[:ok]
    for e in pushed:
        seen.add(e["id"])
    if pushed:
        new_max = max(int(e["id"]) for e in pushed)
        if new_max > max_id:
            max_id = new_max
    if ok < len(notify):
        log.warning("推送中断（成功 %d/%d），剩余下次重试", ok, len(notify))
        state["push_fails"] = int(state.get("push_fails", 0) or 0) + 1
    else:
        state["push_fails"] = 0
        state["alerted_push"] = False

    # 把本轮抓取到的全部 ID 记入 seen（防止后续 max_id 回退时重推历史）
    for e in entries:
        if e.get("id"):
            seen.add(e["id"])

    state["max_id"] = str(max_id)
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
            # 推送端持续失败告警（如 Server酱 免费额度耗尽 / 密钥失效）：
            # 抓取正常但微信收不到时，至少让用户知道推送通道挂了，而不是一片绿却没消息。
            if state.get("push_fails", 0) >= MAX_FAILS_BEFORE_ALERT and not state.get("alerted_push"):
                try:
                    notify_entries([{"id": "_push_alert", "title":
                        f"⚠️ 推送端异常\n连续 {state['push_fails']} 次推送失败"
                        "（可能 Server酱 免费额度耗尽或密钥失效），请检查推送配置",
                        "link": "", "published": ""}])
                except Exception:
                    pass
                state["alerted_push"] = True
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
            # 抓取失败本身不是程序 bug（多为 GitHub 云 IP 被 X 限流等外部因素），
            # 用 exit 0 避免 GitHub Actions 满屏红 ✗；连续失败已达阈值时上面已推微信告警
            sys.exit(0)
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地冒烟测试：无需网络，验证 抓取/去重/推送 逻辑。"""
import os
import sys
import tempfile

# 用临时状态文件，不污染真实环境
_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
os.environ["STATE_FILE"] = _tmp.name
os.environ["WECOM_WEBHOOK"] = "https://example.com/fake"

import monitor  # noqa: E402

# ---- 桩：监控源返回 entries（dict: id/title/link/published）----
FEED_SEQ = []

def fake_fetch_source():
    return [dict(e) for e in FEED_SEQ]

monitor.fetch_source = fake_fetch_source

# ---- 桩：推送，记录调用 ----
CALLS = []
def fake_notify(entries):
    for title, body in monitor.build_messages(entries):
        CALLS.append(body)
    return len(entries)
monitor.notify_entries = fake_notify

def mk(id_, text=None):
    return {
        "id": id_,
        "title": text if text is not None else id_,
        "link": f"https://x.com/binancezh/status/{id_}",
        "published": "2026-01-01T00:00:00Z",
    }

state = monitor.load_state()
assert state.get("first_run") is True

# 第 1 轮：3 条历史
FEED_SEQ = [mk("a", "公告A"), mk("b", "公告B"), mk("c", "公告C")]
n = monitor.run_once(state)
assert n == 0, f"首轮应不推送, got {n}"
assert len(CALLS) == 0, "首轮不应调用推送"
print("[OK] 首轮仅记录历史，不推送")

# 第 2 轮：历史 + 2 条新（d, e）
FEED_SEQ = [mk("e", "新公告E"), mk("d", "新公告D"), mk("a"), mk("b"), mk("c")]
CALLS.clear()
n = monitor.run_once(state)
assert n == 2, f"应推送 2 条新, got {n}"
assert len(CALLS) == 2, f"应调用 2 次推送, got {len(CALLS)}"
assert "新公告D" in CALLS[0] and "新公告E" in CALLS[1], "顺序应为旧→新"
print("[OK] 推送 2 条新动态，顺序 旧→新")

# 第 3 轮：无新，应 0 推送
FEED_SEQ = [mk("e"), mk("d"), mk("a"), mk("b"), mk("c")]
CALLS.clear()
n = monitor.run_once(state)
assert n == 0, f"无新应 0 推送, got {n}"
print("[OK] 无新动态不推送")

# 关键词过滤：只推含"上线"的
monitor.KEYWORD_FILTER = ["上线"]
FEED_SEQ = [mk("f", "项目上线通知"), mk("g", "日常播报"), mk("e"), mk("d"), mk("a"), mk("b"), mk("c")]
CALLS.clear()
n = monitor.run_once(state)
assert n == 1, f"关键词过滤应只推 1 条, got {n}"
assert "上线" in CALLS[0], "应只推含'上线'的"
print("[OK] 关键词过滤生效，仅推送命中关键词的 1 条")

print("\n全部冒烟测试通过")
_tmp.close()
try:
    os.unlink(_tmp.name)
except OSError:
    pass

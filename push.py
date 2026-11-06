#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 briefing_today.md 推送到微信（Server酱）。

固化脚本，供早晚两个自动任务直接调用：python push.py
- 语音版链接放在消息最上面，文案就写「语音版」，开车时一眼就能点；
- 标题自动从早报首行提取（早报/晚报都适配）；
- 从 .env 读取 SERVERCHAN_KEY 与 LISTEN_URL；
- 推送失败会以非零退出码结束，方便自动任务察觉。
"""
import os
import re
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent


def load_env():
    env = {}
    p = BASE / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


env = load_env()
key = (env.get("SERVERCHAN_KEY") or os.environ.get("SERVERCHAN_KEY", "")).strip()

# 语音版链接：本地用 .env 里的 LISTEN_URL（WorkBuddy 发布地址）；
# 跑在 GitHub Actions 上时 .env 不存在，自动推导 GitHub Pages 地址。
listen_url = (env.get("LISTEN_URL") or os.environ.get("LISTEN_URL") or "").strip().strip('"')
if not listen_url:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in repo:
        user, name = repo.split("/", 1)
        listen_url = f"https://{user.lower()}.github.io/{name}/briefing.html"

md_path = BASE / "briefing_today.md"
if not md_path.exists():
    print("ERROR: briefing_today.md 不存在")
    sys.exit(1)
md = md_path.read_text(encoding="utf-8").strip()

first = md.splitlines()[0].lstrip("#").strip()
title = first if first else "财经早报"

# 语音版链接放最上面，只写「语音版」三个字；正文不再重复链接
desp = f"> [语音版]({listen_url})\n\n{md}" if listen_url else md

if not key or key.startswith("你的_"):
    print("ERROR: .env 中 SERVERCHAN_KEY 未配置")
    sys.exit(1)

try:
    r = requests.get(
        f"https://sctapi.ftqq.com/{key}.send",
        params={"title": title, "desp": desp},
        timeout=25,
    )
    print("HTTP", r.status_code, r.text[:400])
    data = r.json()
    if data.get("code") == 0:
        print("PUSH_OK")
        sys.exit(0)
    print("PUSH_FAILED")
    sys.exit(1)
except Exception as e:
    print("ERROR:", repr(e))
    sys.exit(1)

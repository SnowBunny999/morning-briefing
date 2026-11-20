#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 fetch_prices.py 生成的 prices_snippet.md 拼接到 briefing_today.md。
行情块放在主标题之后、新闻正文之前（2026-11-20 用户要求）；先删旧块再插入，幂等。
AI 不写行情段落，价格由本脚本保证准确。"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))
md_path = os.path.join(BASE, "briefing_today.md")
snip_path = os.path.join(BASE, "prices_snippet.md")

md = open(md_path, encoding="utf-8").read()

if os.path.exists(snip_path):
    snip = open(snip_path, encoding="utf-8").read().strip()
    if snip:
        # 1) 删除旧行情块：从「## 💰」到下一个「## 」标题（旧版在文末时则到文末）
        kept, skipping = [], False
        for line in md.splitlines():
            if line.startswith("## 💰"):
                skipping = True
                continue
            if skipping and line.startswith("## "):
                skipping = False
            if not skipping:
                kept.append(line)

        # 2) 行情插到主标题之后，其余正文（今日重点…）原样保留
        title_idx = next((i for i, l in enumerate(kept) if l.startswith("# ")), None)
        if title_idx is None:
            head, body = [], kept
        else:
            head, body = kept[:title_idx + 1], kept[title_idx + 1:]
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        parts = (head + [""] if head else []) + snip.splitlines() + ([""] + body if body else [])
        md = "\n".join(parts).rstrip()
        print("已把行情片段放到标题之后（替换旧位置）")
    else:
        print("行情片段为空，跳过")
else:
    print("未找到 prices_snippet.md，跳过拼接")

with open(md_path, "w", encoding="utf-8") as f:
    f.write(md + "\n")
print("最终早报字数:", len(md))

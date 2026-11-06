#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 fetch_prices.py 生成的 prices_snippet.md 拼接到 briefing_today.md（替换旧的行情块，幂等）。
AI 不写行情段落，价格由本脚本保证准确。"""
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
md_path = os.path.join(BASE, "briefing_today.md")
snip_path = os.path.join(BASE, "prices_snippet.md")

md = open(md_path, encoding="utf-8").read()

if os.path.exists(snip_path):
    snip = open(snip_path, encoding="utf-8").read().strip()
    if snip:
        # 删除旧行情块：从「## 💰」标题到文末整段替换
        md = re.sub(r"\n## 💰.*$", "", md, flags=re.S)
        md = md.rstrip() + "\n\n" + snip
        print("已拼接/更新行情片段")
    else:
        print("行情片段为空，跳过")
else:
    print("未找到 prices_snippet.md，跳过拼接")

with open(md_path, "w", encoding="utf-8") as f:
    f.write(md + "\n")
print("最终早报字数:", len(md))

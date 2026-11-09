#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仅负责采集：调用 briefing.py 的采集层，把原始新闻条目导出为 collected.json。
写稿由 daily_run.py + editorial.py 完成，本脚本不调用任何 LLM。"""
import json
import sys
import yaml

sys.path.insert(0, ".")
import briefing  # noqa: E402

cfg = yaml.safe_load(open(briefing.CONFIG_PATH, encoding="utf-8"))
hours = int(cfg.get("time_window_hours", 18))
max_items = int(cfg.get("max_items", cfg.get("max_items_for_llm", 120)))

all_items = []
for src in cfg.get("sources", []):
    all_items += briefing.fetch_source(src, hours)

all_items.sort(key=lambda x: x.get("age_hours", 9999))
top = all_items[:max_items]

with open("collected.json", "w", encoding="utf-8") as f:
    json.dump(top, f, ensure_ascii=False, indent=2)

print(f"共采集 {len(top)} 条原始新闻 -> collected.json")
for i in top:
    print(f"  [{i['region']}/{i['source']}] {i['title']}")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集层：把配置里的 RSS / JSON 信源抓成统一结构，交给 collect.py 写 collected.json。

统一字段：source / region / title / summary / body / link / age_hours
- body 是「尽量完整的正文」：RSS 取 content:encoded 或 description 全文，
  JSON 源取 body_field 或 summary_field 全文。写稿层还会再抓原文补正文。
- 单个源失败自动跳过，绝不影响整份早报。
- 不调用任何 LLM，也不需要任何密钥。
"""
import datetime
import json
import os
import re

import feedparser
import requests
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_headers(source):
    h = {"User-Agent": UA}
    if source.get("headers"):
        h.update(source["headers"])
    return h


def plain(s):
    s = re.sub(r"<[^>]*>", " ", s or "")
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _age(value, fmt, now):
    """返回新闻距今小时数；无法解析返回 -1（未知时间，不否决、也不给新鲜度加分）。"""
    if value is None:
        return -1.0
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            return (now - dt).total_seconds() / 3600.0
        if isinstance(value, str) and value.strip():
            s = value.strip()
            if fmt:
                dt = datetime.datetime.strptime(s, fmt)
            else:
                dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
            return (now - dt.astimezone(datetime.timezone.utc)).total_seconds() / 3600.0
    except Exception:
        pass
    return -1.0


def _title_from_body(body, limit=70):
    b = plain(body)
    first = re.split(r"[。！？；\n]", b)[0].strip()
    return first[:limit].rstrip("，,、 ") if len(first) >= 8 else ""


def _item(source, title, summary, body, link, age):
    return {
        "source": source.get("name", "unknown"),
        "region": source.get("region", "global"),
        "title": plain(title),
        "summary": plain(summary)[:300],
        "body": plain(body)[:3000],
        "link": str(link or "").strip(),
        "age_hours": round(age, 1),
    }


def fetch_feed(source, hours_window):
    name = source.get("name", "unknown")
    win = int(source.get("window_hours", hours_window))
    try:
        resp = requests.get(source["url"], headers=_build_headers(source), timeout=12)
        resp.encoding = resp.apparent_encoding or "utf-8"
        parsed = feedparser.parse(resp.text)
        if parsed.bozo and not parsed.entries:
            print(f"[warn] 解析失败或无内容：{name}")
            return []
        now = datetime.datetime.now(datetime.timezone.utc)
        items = []
        for e in parsed.entries:
            content = e.get("content") or []
            encoded = content[0].get("value", "") if isinstance(content, list) and content else ""
            summary = e.get("summary") or e.get("description") or ""
            body = encoded or summary
            title = (e.get("title") or "").strip()
            if not title and source.get("title_from_body"):
                title = _title_from_body(body)
            if not title:
                continue
            pub = e.get("published_parsed") or e.get("updated_parsed")
            age = -1.0
            if pub:
                pdt = datetime.datetime(*pub[:6], tzinfo=datetime.timezone.utc)
                age = (now - pdt).total_seconds() / 3600.0
            if age < 0 or age <= win:
                items.append(_item(source, title, summary, body, e.get("link", ""), age))
        print(f"[ok] {name}：{len(items)} 条（窗口 {win}h）")
        return items
    except Exception as ex:
        print(f"[error] {name}：{ex}")
        return []


def fetch_json_source(source, hours_window):
    name = source.get("name", "unknown")
    win = int(source.get("window_hours", hours_window))
    try:
        resp = requests.get(source["url"], headers=_build_headers(source), timeout=12)
        resp.encoding = resp.apparent_encoding or "utf-8"
        data = resp.json()

        def dig(node, path):
            for key in path.split("."):
                if isinstance(node, dict):
                    node = node.get(key)
                else:
                    return None
            return node

        node = dig(data, source.get("items_path", ""))
        if isinstance(node, dict):
            for v in node.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    node = v
                    break
        if not isinstance(node, list):
            print(f"[warn] {name}：未找到列表字段，跳过")
            return []

        now = datetime.datetime.now(datetime.timezone.utc)
        tf = source.get("time_field")
        tfmt = source.get("time_format")
        title_field = source.get("title_field", "title")
        summary_field = source.get("summary_field")
        body_field = source.get("body_field") or summary_field
        link_field = source.get("link_field", "url")
        items = []
        for raw in node:
            if not isinstance(raw, dict):
                continue
            body = raw.get(body_field) or "" if body_field else ""
            summary = raw.get(summary_field) or "" if summary_field else ""
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False)
            title = (raw.get(title_field) or "").strip()
            if not title:
                title = _title_from_body(body or summary) if source.get("title_from_body") else ""
            if not title:  # 兜底：取第一个够长的字符串字段
                for v in raw.values():
                    if isinstance(v, str) and len(v) >= 8:
                        title = v.strip()[:80]
                        break
            if not title:
                continue
            age = _age(raw.get(tf) if tf else None, tfmt, now)
            if age < 0 or age <= win:
                items.append(_item(source, title, summary, body or summary,
                                   raw.get(link_field, ""), age))
        print(f"[ok] {name}：{len(items)} 条（窗口 {win}h）")
        return items
    except Exception as ex:
        print(f"[error] {name}：{ex}")
        return []


def fetch_source(source, hours_window):
    if source.get("type") == "json":
        return fetch_json_source(source, hours_window)
    return fetch_feed(source, hours_window)

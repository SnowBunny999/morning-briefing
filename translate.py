#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把英文新闻标题翻成中文（MyMemory 免费接口，无需注册/密钥）。

- 带本地缓存 translate_cache.json，同一标题只翻译一次，避免反复消耗免费额度；
- 失败一律返回原文，绝不阻塞早报生成；
- 只翻「以英文为主」的标题，中文标题不调用接口。
"""
import json
import os
import re
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "translate_cache.json")


def _cache_load():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(d):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def needs_translation(text):
    """判断标题是否以英文为主。"""
    if not text:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[A-Za-z]", text))
    return en > 12 and en > cn * 2


def translate(text, timeout=12, retries=2):
    """英文→中文；失败或超限自动重试，最终仍失败则返回原文。"""
    if not needs_translation(text):
        return text
    cache = _cache_load()
    if text in cache:
        return cache[text]
    for attempt in range(retries + 1):
        try:
            url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(
                {"q": text[:450], "langpair": "en|zh-CN"}
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            out = (data.get("responseData") or {}).get("translatedText") or ""
            bad = ("MYMEMORY WARNING", "QUERY LENGTH LIMIT", "INVALID", "PLEASE SELECT")
            if not out or any(b in out.upper() for b in bad) or out.strip() == text:
                raise RuntimeError("bad response")
            out = out.replace("&quot;", '"').replace("&#39;", "'").strip()
            cache[text] = out
            _cache_save(cache)
            return out
        except Exception:
            if attempt < retries:
                import time
                time.sleep(0.8 * (attempt + 1))
    return text


def translate_many(texts, limit=40):
    """并发翻译，返回 {原文: 译文}；限制条数保护免费额度、避免拖慢流水线。"""
    from concurrent.futures import ThreadPoolExecutor

    todo = [t for t in texts if needs_translation(t)][:limit]
    # 已缓存的不再请求
    cache = _cache_load()
    pending = [t for t in todo if t not in cache]
    if pending:
        # 免费接口并发过高会限流，压到 3 并发 + 串行补漏
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(translate, pending))
        cache = _cache_load()
        for t in pending:
            if t not in cache:      # 仍失败的再串行试一次
                translate(t)
    cache = _cache_load()
    return {t: cache.get(t, t) for t in texts}

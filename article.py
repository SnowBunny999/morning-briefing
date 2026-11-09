#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原文正文抓取：RSS 大多只给标题和一句话摘要，做不出「不点原文也能读懂」的概括。

本模块在新闻入选后，并发抓取原文网页的正文段落，交给 editorial.py 做抽取式概括。
- 不引入任何密钥或新接口，只是普通 HTTP GET；
- 单条失败静默跳过，不影响整份早报；
- 英文正文会保留原文，由 translate.py 只翻译挑中的几句（省额度）。
"""
import re
from concurrent.futures import ThreadPoolExecutor

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 网页里常见的非正文噪音（导航、订阅、免责、版权）
NOISE = re.compile(
    r"(Please enable JS|disable any ad blocker|Sign up|Subscribe|Read more|"
    r"Official websites use \.gov|A \.gov website belongs to|Secure \.gov websites|"
    r"All rights reserved|Copyright|Cookie|Advertisement|点击(?:阅读|查看)更多|"
    r"扫描二维码|关注我们|免责声明|版权所有|未经授权|点击上方)", re.I)


def plain(s):
    """去 HTML / 实体 / 多余空白。"""
    s = re.sub(r"<[^>]*>", " ", s or "")
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_body(url, timeout=10):
    """抓一篇文章的正文，返回纯文本（失败返回空串）。"""
    if not url or not str(url).startswith("http"):
        return ""
    # 已知 JS 空壳页（华尔街见闻快讯），正文已在 API 的 content_text 里，别浪费请求
    if "wallstreetcn.com/livenews/" in url:
        return ""
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, headers={"User-Agent": UA,
                                      "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                        timeout=timeout)
        if r.status_code != 200 or len(r.text) < 800:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "header", "footer", "aside",
                       "form", "noscript", "figure", "figcaption"]):
            t.decompose()

        def paragraphs(root):
            out = []
            for p in root.find_all("p"):
                s = plain(p.get_text(" ", strip=True))
                if len(s) >= 25 and not NOISE.search(s[:120]):
                    out.append(s)
            return out

        best = []
        for sel in ("article", "[class*=article-body]", "[class*=articleBody]",
                    "[class*=article-content]", "[class*=content]", "main"):
            try:
                for node in soup.select(sel):
                    ps = paragraphs(node)
                    if len(ps) > len(best):
                        best = ps
            except Exception:
                continue
        if len("".join(best)) < 200:
            best = paragraphs(soup)
        text = "\n".join(best[:24])
        return text[:2600]
    except Exception:
        return ""


def sentences(text, max_n=40):
    """中英混合切句，过滤太短/纯噪音的句子。"""
    t = plain(text)
    # 中文标点后直接断句（句号后通常没有空格）；英文句号要求后面有空格 + 大写起首，
    # 避免把 "U.S." / "3.5%" 这类切开。
    parts = re.split(r"(?<=[。！？；!?])|(?<=[.!?])\s+(?=[A-Z\"'])", t)
    out = []
    for p in parts:
        s = p.strip(" ，,、；;。.！!？?\"'")
        s = re.sub(r"\s+", " ", s)
        if len(s) < 12 or NOISE.search(s[:100]):
            continue
        if s not in out:
            out.append(s)
        if len(out) >= max_n:
            break
    return out


def is_english(s):
    en = len(re.findall(r"[A-Za-z]", s or ""))
    cn = len(re.findall(r"[\u4e00-\u9fff]", s or ""))
    return en > 12 and en > cn * 2


def enrich(items, log=None, max_workers=5, need=180):
    """给正文太短的条目补抓原文。只处理传入的这批（入选后的 12~15 条），不拖慢采集。"""
    say = log or (lambda m: None)
    todo = [it for it in items
            if len(plain(it.get("body") or "")) < need and (it.get("link") or "").startswith("http")]
    if not todo:
        say("  正文抓取：无需补抓")
        return 0

    def work(it):
        return fetch_body(it.get("link", ""))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        bodies = list(ex.map(work, todo))
    n = 0
    for it, b in zip(todo, bodies):
        if b and len(b) > len(plain(it.get("body") or "")):
            it["body"] = b
            it["_fetched"] = True
            n += 1
    say(f"  正文抓取：{len(todo)} 条待抓，成功 {n} 条")
    return n

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""早报「逐块」解析：网页显示、语音文本、同步高亮共用同一套分块。

约定（2026-11-06 用户要求）：
- 朗读时不读 emoji 图标、不读「（来源：xxx）」；网页里仍保留但弱化显示。
- 以 `> ` 开头的行是「详情块」：新闻摘要 + 原文链接，网页/微信可见，但**不朗读**
  （开车听的时候不被细节拖长），且与上一条新闻共用高亮序号，跟着一起亮。
"""
import html as html_mod
import re

# 覆盖常见 emoji、装饰符号、变体选择符
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\u200d\u20e3]"
)
SRC_RE = re.compile(r"[（(]来源\s*[:：]\s*[^）)]*[）)]")


def _strip_md(s):
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)   # 链接只留文字
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    return s


def for_speech(display):
    """生成朗读用纯文本：去来源、去图标、去 markdown 符号、去掉无信息量的「中性」。"""
    s = SRC_RE.sub("", display)
    s = _strip_md(s)
    s = EMOJI.sub("", s)
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[。.]?\s*中性\s*$", "", s)
    return s


def parse(md):
    """返回 [{'kind','display','speak'}]；空行跳过。
    detail 块 speak 为空字符串 -> 不参与语音生成，但保留在网页里。"""
    out = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^>\s?", line):
            out.append({
                "kind": "detail",
                "display": re.sub(r"^>\s?", "", line),
                "speak": "",
            })
            continue
        kind, text = "p", line
        if re.match(r"^##\s", line):
            kind, text = "h2", re.sub(r"^##\s+", "", line)
        elif re.match(r"^#\s", line):
            kind, text = "h1", re.sub(r"^#\s+", "", line)
        elif re.match(r"^[-*]\s+", line):
            kind, text = "li", re.sub(r"^[-*]\s+", "", line)
        speak = for_speech(text)
        if not speak:
            continue
        out.append({"kind": kind, "display": text, "speak": speak})
    return out


def sentence(block):
    """把一块压成恰好一句，让 edge-tts 的 SentenceBoundary 与块一一对应。"""
    s = block["speak"]
    s = s.replace("。", "，").replace("！", "，").replace("？", "，")
    s = s.rstrip("，,；; ")
    return (s or "略") + "。"


def inline(text):
    """显示用 HTML：来源小字灰显，保留粗体/链接/emoji。"""
    t = html_mod.escape(text)
    t = SRC_RE.sub(lambda m: f'<span class="src">{m.group(0)}</span>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", t)
    return t


def render_cues(blocks):
    """生成网页块。detail 块不占朗读序号，data-i 跟随上一条新闻一起高亮。"""
    out, last_readable = [], 0
    for i, b in enumerate(blocks):
        if b["kind"] == "detail":
            out.append(
                f'<div class="cue detail" data-i="{last_readable}">{inline(b["display"])}</div>'
            )
            continue
        tag = {"h1": "h1", "h2": "h2"}.get(b["kind"], "div")
        cls = {
            "h1": "cue h1", "h2": "cue h2", "quote": "cue quote",
            "li": "cue li",
        }.get(b["kind"], "cue p")
        out.append(f'<{tag} class="{cls}" data-i="{i}">{inline(b["display"])}</{tag}>')
        last_readable = i
    return "\n".join(out)

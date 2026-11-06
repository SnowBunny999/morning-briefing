#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 briefing.mp3 + briefing.cues.json（逐条时间轴，用于网页同步高亮）。

- 女声 XiaoxiaoNeural（微软神经网络语音，免费无需密钥）；
- 朗读文本来自 briefing_common，已剔除 emoji 图标与「（来源：xxx）」；
- 「详情块」（摘要 + 原文链接）不参与朗读，避免开车时啰嗦，但仍显示在网页上
  并与所属新闻共用高亮序号；
- 用 edge-tts 的 WordBoundary 事件算出每一条新闻的起止秒数；
- 任何失败都静默跳过（删除 mp3），render 会退回浏览器朗读兜底，不阻塞流水线。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from briefing_common import parse, sentence  # noqa: E402

VOICE = "zh-CN-XiaoxiaoNeural"
MP3 = "briefing.mp3"
CUES = "briefing.cues.json"


def build_cues(readable, bounds, text_len, total_sec):
    """把词级时间边界映射回「每一个朗读块」的起止秒数。
    readable = [(原 block 索引, block), ...]，cues 里的 i 用原索引，
    这样网页 detail 块跟随的 data-i 才能对上。"""
    if not bounds or not text_len:
        return []
    covered = bounds[-1][1] if bounds else 0
    scale = covered / text_len if covered else 1.0
    cues, pos = [], 0
    for idx, b in readable:
        n = len(sentence(b))
        a, z = pos * scale, (pos + n) * scale
        pos += n
        hit = [w for w in bounds if w[1] > a and w[0] < z]
        if hit:
            start, end = hit[0][2], hit[-1][3]
        else:
            start = total_sec * (pos - n) / max(text_len, 1)
            end = total_sec * pos / max(text_len, 1)
        cues.append({"i": idx, "start": round(start, 2),
                     "end": round(max(end, start + 0.2), 2)})
    for j in range(1, len(cues)):
        if cues[j]["start"] < cues[j - 1]["end"]:
            cues[j]["start"] = cues[j - 1]["end"]
        if cues[j]["end"] <= cues[j]["start"]:
            cues[j]["end"] = cues[j]["start"] + 0.2
    return cues


def main():
    try:
        import edge_tts
    except Exception as e:
        print(f"[跳过] 未安装 edge_tts：{e}")
        return

    if not os.path.exists("briefing_today.md"):
        print("[跳过] 找不到 briefing_today.md")
        return

    md = open("briefing_today.md", encoding="utf-8").read()
    blocks = parse(md)
    readable = [(i, b) for i, b in enumerate(blocks) if b["speak"]]
    if not readable:
        print("[跳过] 无可读文本")
        return
    text = "".join(sentence(b) for _, b in readable)

    for p in (MP3, CUES):
        if os.path.exists(p):
            os.remove(p)

    async def run():
        comm = edge_tts.Communicate(text, VOICE, boundary="WordBoundary")
        audio, bounds, charpos = bytearray(), [], 0
        async for ch in comm.stream():
            if ch["type"] == "audio":
                audio.extend(ch.get("data") or b"")
            elif ch["type"] == "WordBoundary":
                t = ch.get("text") or ""
                off = (ch.get("offset") or 0) / 1e7
                dur = (ch.get("duration") or 0) / 1e7
                bounds.append((charpos, charpos + len(t), off, off + dur))
                charpos += len(t)
        return bytes(audio), bounds

    try:
        audio, bounds = asyncio.run(run())
        if not audio:
            raise RuntimeError("未收到音频数据")
        with open(MP3, "wb") as f:
            f.write(audio)
        total = bounds[-1][3] if bounds else 0
        cues = build_cues(readable, bounds, len(text), total)
        with open(CUES, "w", encoding="utf-8") as f:
            json.dump(cues, f, ensure_ascii=False)
        print(f"[ok] 已生成 {MP3}（{len(audio)} 字节）与 {CUES}"
              f"（{len(cues)} 条时间轴 / {len(blocks)} 个网页块，总时长 {total:.1f}s，{VOICE}）")
    except Exception as e:
        print(f"[警告] 生成语音失败，将退回浏览器朗读：{e}")
        for p in (MP3, CUES):
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    main()

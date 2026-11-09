#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键流水线：采集 → 行情 → 写稿 → 拼接 → 【推送微信】 → 语音 → 网页 → 日志。

写稿（2026-11-09 版）：
- 只收能挂到资产上的新闻（A股/美股/股价/市值/汇率/黄金/利率/政策/大科技公司财报），
  国际时事、教程、营销标题、概念宣传由 editorial.py 硬门槛剔除。
- 每条先抓原文正文，再从原文抽出 2-3 句组成「不点原文也能读懂整件事」的概括。
  概括写成普通段落：微信里能读到、语音里也会朗读；来源和原文链接仍放在 `> ` 详情行，
  不朗读。
- 全程不依赖任何 LLM / 新密钥；正文抓取与译中失败都会自动降级，绝不阻塞推送。
- 推送微信排在语音/网页之前，任何后段失败都不影响你收到消息。

用法：
  python daily_run.py --mode morning
  python daily_run.py --mode evening
  python daily_run.py --mode morning --skip-push --skip-media   # 本地演练
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / "run.log"
BJ = datetime.timezone(datetime.timedelta(hours=8))

POS = ("涨", "升", "超预期", "利好", "增长", "上调", "反弹", "收高", "走高", "创新高",
       "扩大", "回升", "盈利", "获批", "突破", "站上", "获利")
NEG = ("跌", "降", "不及预期", "利空", "下滑", "下调", "亏损", "收低", "走低", "萎缩",
       "承压", "警示", "减持", "风险", "下挫", "立案", "索赔")

TARGET_RULES = (
    (("黄金", "金价", "贵金属", "白银", "XAU"), "黄金"),
    (("原油", "油价", "OPEC", "欧佩克", "布伦特", "WTI", "产油", "减产", "页岩", "油气"), "油价"),
    (("比特币", "以太坊", "加密货币", "数字货币", "BTC", "ETH"), "币价"),
    (("美元指数", "汇率", "人民币", "美债", "国债收益率"), "汇率"),
    (("日经", "日韩", "KOSPI", "亚太", "欧洲股市"), "亚太股市"),
    (("纳斯达克", "标普", "道指", "美股", "华尔街"), "美股"),
    (("恒生", "港股", "恒指"), "港股"),
    (("A股", "沪指", "创业板", "上证", "深证", "沪深", "北向", "两市"), "A股"),
    (("央行", "美联储", "联储", "利率", "通胀", "非农", "CPI", "PMI", "GDP",
      "关税", "衰退", "政策"), "全球市场"),
)


def now_bj():
    """GitHub Actions 服务器是 UTC，统一按北京时间取日期/判断早晚。"""
    return datetime.datetime.now(BJ)


def log(msg):
    line = f"[{now_bj():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(script, timeout=180):
    """运行一个子脚本，失败只记录、不抛出。"""
    try:
        p = subprocess.run(
            [sys.executable, script], cwd=BASE, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (p.stdout or "").strip()
        if out:
            log(f"  {script}: {out[-400:]}")
        if p.returncode != 0:
            log(f"  {script} 退出码={p.returncode} stderr={(p.stderr or '')[-240:]}")
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        log(f"  {script} 超时({timeout}s)，跳过")
        return False
    except Exception as e:
        log(f"  {script} 异常: {e}")
        return False


def clean(text, limit=120):
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r"[\[\]{}<>|`]", "", text)
    return text[:limit]


def clean_summary(s, limit=260):
    s = re.sub(r"<[^>]*>", "", s or "")
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[<＜]\s*[.．…]{1,3}\s*$", "", s)
    s = re.sub(r"[.．…]{2,}\s*$", "", s)
    s = re.sub(r"^(人民财讯|财联社|新浪财经|证券时报)\s*\d{1,2}月\d{1,2}日电[，,]?\s*", "", s)
    return s.strip(" 。，,；;·")[:limit]


def extract_link(raw):
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.startswith("{") or s.startswith("["):
        try:
            obj = json.loads(s)

            def find(o):
                if isinstance(o, dict):
                    for k in ("url", "link", "h5Url", "target", "href"):
                        v = o.get(k)
                        if isinstance(v, str) and v.startswith("http"):
                            return v
                    for v in o.values():
                        r = find(v)
                        if r:
                            return r
                elif isinstance(o, list):
                    for v in o:
                        r = find(v)
                        if r:
                            return r
                return ""
            return find(obj)
        except Exception:
            pass
    m = re.search(r"https?://[^\s\"'<>）)]+", s)
    return m.group(0) if m else ""


def safe_url(u):
    return (u or "").replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def direction(text):
    # 「涨跌互现 / 冲高回落 / 震荡」不是方向信号，先剔除再判断，避免误标利空
    t = re.sub(r"(涨跌互现|涨跌不一|有涨有跌|震荡|波动|冲高回落|高位回落|互现)", "", text or "")
    if any(k in t for k in NEG):
        return "利空"
    if any(k in t for k in POS):
        return "利好"
    return "中性"


def target_of(it, blob=None, force=None):
    if force:
        return force
    title = it.get("title", "")
    full = blob if blob is not None else (title + " " + it.get("summary", ""))
    for s in (title, full):
        for keys, name in TARGET_RULES:
            if any(k in s for k in keys):
                return name
    return "海外市场" if it.get("region") == "global" else "A股"


def impact(it, force_target=None):
    # 只看标题判断方向，避免概括里顺带提到的「承压」乱带节奏
    d = direction(it.get("title", ""))
    if d == "中性":
        return "中性"
    tgt = target_of(it, it.get("title", ""), force_target)
    if tgt == "油价":
        return "利多油价" if d == "利好" else "利空油价"
    return f"{d}{tgt}"


def build_briefing(mode):
    """结构化写稿：译中 → 相关性硬门槛 → 抓正文 → 完整概括 → 分栏目成稿。"""
    today = now_bj().strftime("%Y-%m-%d")
    label = "财经早报" if mode == "morning" else "财经晚报"
    md_path = BASE / "briefing_today.md"

    items = []
    cp = BASE / "collected.json"
    if cp.exists():
        try:
            items = json.loads(cp.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"  读取 collected.json 失败: {e}")
    if not isinstance(items, list):
        items = []

    for it in items:
        it["title"] = clean(it.get("title", ""), 120)
        it["summary"] = clean_summary(it.get("summary", ""), 260)

    # 英文标题必须先译中，否则中文资产词表无法判断相关性
    import translate
    try:
        titles = list(dict.fromkeys(x["title"] for x in items if x.get("title")))
        cmap = translate.translate_many(titles, limit=160)
        n_tr = 0
        for it in items:
            t = it.get("title", "")
            if cmap.get(t) and cmap[t] != t:
                it["title"] = cmap[t]
                n_tr += 1
        log(f"  英文标题译中：{n_tr} 处")
    except Exception as e:
        log(f"  标题译中跳过：{e}")

    def tr_many(texts):
        try:
            return translate.translate_many(texts, limit=140)
        except Exception as e:
            log(f"  正文译中跳过：{e}")
            return {}

    try:
        import article
        import editorial
        focus, rest, stats = editorial.select(
            items, log=log, translate_fn=tr_many, enrich_fn=article.enrich
        )
    except Exception:
        log("  写稿异常: " + traceback.format_exc()[-500:])
        focus, rest, stats = [], [], {"picked": 0}

    def render_event(ev):
        src = clean("/".join(ev["sources"][:2]), 40)
        ctx = dict(ev["raw"])
        ctx["title"] = ev["title"]
        lines = [f"- {ev['title']}（来源：{src}）。{impact(ctx)}"]
        # 概括是普通段落：微信里能读到，语音里也会朗读
        lines += [p for p in ev["para"] if p]
        links = [f"[原文{i + 1}]({safe_url(extract_link(u))})"
                 for i, u in enumerate(ev["links"][:2]) if extract_link(u)]
        if links:
            lines.append("> " + " · ".join(links))
        return lines

    out = [f"# 📈 {label} · {today}", "", "## 今日重点", ""]
    for ev in focus:
        out += render_event(ev) + [""]
    if not focus:
        out += ["- 今日暂无符合投资相关标准的重点消息", ""]

    groups = {}
    for ev in rest:
        groups.setdefault(ev["section"], []).append(ev)
    for key in editorial.SECTION_ORDER:
        evs = groups.get(key, [])
        if not evs:
            continue
        out += ["", editorial.SECTION_TITLE[key], ""]
        for ev in evs[:5]:
            out += render_event(ev) + [""]

    md_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    n_para = sum(len(ev["para"]) for ev in focus + rest)
    avg = (sum(len("".join(ev["para"])) for ev in focus + rest)
           / max(1, len(focus) + len(rest)))
    log(f"  结构化生成{label}：采用 {stats.get('picked', 0)} 条（重点 {len(focus)}），"
        f"概括 {n_para} 句 / 平均 {avg:.0f} 字；剔除 {stats.get('offtopic', 0)} 条非投资类、"
        f"{stats.get('no_para', 0)} 条概括不足")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("morning", "evening"), default=None)
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--skip-media", action="store_true")
    args = ap.parse_args()
    mode = args.mode or ("morning" if now_bj().hour < 12 else "evening")

    log(f"===== 开始 {mode} 流水线 =====")
    os.chdir(BASE)

    run("collect.py", timeout=300)
    run("fetch_prices.py", timeout=240)
    try:
        build_briefing(mode)
    except Exception:
        log("  写稿异常: " + traceback.format_exc()[-500:])
    run("assemble.py", timeout=60)

    # —— 推送优先：微信必须最先送达，后面语音/网页即使全断也不影响 ——
    if args.skip_push:
        log("  [演练] 跳过推送")
        push_ok = None
    else:
        push_ok = run("push.py", timeout=90)
        if not push_ok:
            log("  推送失败，重试一次")
            push_ok = run("push.py", timeout=90)
        log(f"  >>> 推送结果: {'成功(PUSH_OK)' if push_ok else '失败'} <<<")

    if not args.skip_media:
        run("generate_audio.py", timeout=300)
        run("render_briefing.py", timeout=60)

    prices_ok = []
    try:
        p = json.loads((BASE / "prices.json").read_text(encoding="utf-8"))
        prices_ok = [k for k in ("spx", "ixic", "gold", "btc", "eth", "oil_wti", "oil_brent") if p.get(k)]
    except Exception:
        pass
    n_news = 0
    try:
        n_news = len(json.loads((BASE / "collected.json").read_text(encoding="utf-8")))
    except Exception:
        pass

    log(f"===== 结束 {mode} | 采集 {n_news} 条 | 行情成功 {len(prices_ok)}/7 "
        f"({','.join(prices_ok) or '无'}) | 推送 {push_ok} =====")
    if push_ok is False:
        sys.exit(1)


if __name__ == "__main__":
    main()

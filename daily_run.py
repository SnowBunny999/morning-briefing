#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键流水线：采集 → 行情 → 写稿 → 拼接 → 【推送微信】 → 语音 → 网页 → 日志。

设计要点：
- 把「推送微信」提到语音/网页之前，且全部逻辑都在本脚本内完成，
  不依赖 AI 在定时任务里分步调用，杜绝长流程末尾被截断导致漏发。
- 自动任务只需执行 `python daily_run.py --mode morning` 这一条命令。
- 全程异常不中断，任何一步失败都继续往下走；推送失败自动重试一次。
- 每条新闻：主行（标题 + 来源 + 带对象的影响标注）+ 详情行（摘要 + 原文链接）。
  详情行以 `> ` 开头，网页/微信可见但**不朗读**，开车听不被细节拖长。

用法：
  python daily_run.py --mode morning             # 早报
  python daily_run.py --mode evening             # 晚报
  python daily_run.py --mode morning --skip-push # 演练，不发微信
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

POS = ("涨", "升", "超预期", "利好", "增长", "上调", "反弹", "收高", "走高", "创新高",
       "扩大", "回升", "盈利", "获批", "突破", "站上", "获利")
NEG = ("跌", "降", "不及预期", "利空", "下滑", "下调", "亏损", "收低", "走低", "萎缩",
       "承压", "警示", "减持", "风险", "下挫", "修正区间", "立案", "索赔")

# 分类关键词（只看标题，避免摘要里的词把新闻塞错栏目）
OIL_KW = ("原油", "油价", "OPEC", "欧佩克", "伊朗", "中东", "沙特", "天然气", "页岩",
          "WTI", "布伦特", "产油", "减产", "能源政策", "能源股", "油气")
MACRO_KW = ("央行", "联储", "美联储", "通胀", "利率", "加息", "降息", "关税", "财政",
            "选举", "政策", "国债", "GDP", "就业", "PMI", "白宫", "国务院", "证监会",
            "财政部", "监管", "制裁", "停火", "衰退", "刺激", "弹劾", "中期选举")
CMD_KW = ("黄金", "金价", "白银", "铜", "大宗", "商品", "原油", "油价", "天然气", "贵金属")
CAL_KW = ("经济数据", "公布", "会议", "纪要", "日程", "财报", "非农", "CPI", "PMI",
          "议息", "开盘", "收盘", "预告", "数据出炉")

# 「今日重点」的重要性维度（详见文末说明，属自定义启发式规则，非行业标准）：
#   ① 影响面：宏观/政策/主要指数 > 行业板块 > 个股公告
#   ② 时效性：越新越高
#   ③ 信源：全球主流媒体略加权，并用「同一来源最多 N 条」强制多样性
BREADTH_MACRO = ("央行", "美联储", "联储", "利率", "加息", "降息", "通胀", "关税",
                 "选举", "中期选举", "地缘", "伊朗", "中东", "OPEC", "欧佩克", "衰退",
                 "非农", "CPI", "GDP", "标普", "纳指", "纳斯达克", "道指", "美股",
                 "白宫", "特朗普", "国务院", "证监会", "财政部", "监管", "制裁",
                 "停火", "战争", "政策", "美元", "美债", "弹劾")
BREADTH_SECTOR = ("板块", "行业", "产业链", "原油", "油价", "黄金", "金价", "地产",
                  "新能源", "半导体", "银行股", "券商", "医药")
BREADTH_COMPANY = ("回购", "增持", "减持", "增资", "拟向", "涨超", "跌超", "盘中",
                   "上会", "IPO", "中标", "战略合作", "评级", "耗资", "注销", "万股",
                   "港元", "奖励股份", "预中标", "公告")

# 影响对象：从具体到宽泛依次匹配，决定「利好黄金 / 利空美股 / 利多油价 …」
TARGET_RULES = (
    (("黄金", "金价", "贵金属", "白银", "XAU"), "黄金"),
    (("原油", "油价", "OPEC", "欧佩克", "布伦特", "WTI", "产油", "减产", "页岩", "油气"), "油价"),
    (("比特币", "以太坊", "加密货币", "数字货币", "BTC", "ETH"), "币价"),
    (("美元指数", "美元", "汇率", "人民币", "美债", "国债收益率"), "美元"),
    (("日经", "日韩", "韩国", "KOSPI", "亚太", "欧洲股市"), "亚太股市"),
    (("纳斯达克", "标普", "道指", "美股", "华尔街", "三大指数"), "美股"),
    (("恒生", "港股", "恒指"), "港股"),
    (("A股", "沪指", "创业板", "上证", "深证", "沪深", "北向", "两市"), "A股"),
    (("央行", "美联储", "联储", "利率", "通胀", "非农", "CPI", "PMI", "GDP",
      "关税", "制裁", "选举", "衰退", "白宫", "政策"), "全球市场"),
)


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
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
            log(f"  {script}: {out[-300:]}")
        if p.returncode != 0:
            log(f"  {script} 退出码={p.returncode} stderr={(p.stderr or '')[-200:]}")
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        log(f"  {script} 超时({timeout}s)，跳过")
        return False
    except Exception as e:
        log(f"  {script} 异常: {e}")
        return False


def clean(text, limit=95):
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r"[\[\]{}<>|`]", "", text)
    return text[:limit]


def clean_summary(s, limit=160):
    """把源里的摘要洗成 1-2 句可读文字。"""
    s = re.sub(r"<[^>]*>", "", s or "")
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[<＜]\s*[.．…]{1,3}\s*$", "", s)   # 源自带的 "<..." 截断符
    s = re.sub(r"[.．…]{2,}\s*$", "", s)             # 尾部省略号
    s = re.sub(r"^(人民财讯|财联社|新浪财经|证券时报)\s*\d{1,2}月\d{1,2}日电[，,]?\s*", "", s)
    s = s.strip(" 。，,；;·")
    return s[:limit]


def extract_link(raw):
    """提取原文链接。长城证券把 URL 包在 JSON 字符串里，其余源是裸 URL。"""
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
    """避免 URL 里的括号破坏 markdown 链接语法。"""
    return (u or "").replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def direction(text):
    if any(k in text for k in NEG):
        return "利空"
    if any(k in text for k in POS):
        return "利好"
    return "中性"


def target_of(it, blob=None, force=None):
    """先看标题（最准），标题没命中再看摘要，避免摘要里顺带提到的市场抢走标注对象。"""
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
    """带对象的影响标注：利好黄金 / 利空美股 / 利多油价 / 中性。"""
    blob = it.get("title", "") + " " + it.get("summary", "")
    d = direction(blob)
    if d == "中性":
        return "中性"
    tgt = target_of(it, blob, force_target)
    if tgt == "油价":
        return ("利多油价" if d == "利好" else "利空油价")
    return f"{d}{tgt}"


def detail_line(it, with_summary=True):
    """生成不朗读的详情行：摘要 + 原文链接。"""
    parts = []
    title = clean(it.get("title", ""))
    if with_summary:
        s = clean_summary(it.get("summary", ""), 160)
        # 摘要基本等于标题时不重复
        if s and s not in title and len(s.replace(title[:10], "")) > 18:
            parts.append(s)
    url = safe_url(extract_link(it.get("link", "")))
    if url:
        parts.append(f"[原文]({url})")
    return ("> " + "  ".join(parts)) if parts else ""


def build_briefing(mode):
    """规则化写稿：不依赖任何 LLM，保证无人值守也能出稿。
    若已存在「当天 + 对应早报/晚报」的稿件，则直接复用，不覆盖。"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    label = "财经早报" if mode == "morning" else "财经晚报"
    md_path = BASE / "briefing_today.md"

    if md_path.exists():
        old = md_path.read_text(encoding="utf-8")
        first = old.splitlines()[0] if old.splitlines() else ""
        if today in first and label in first:
            log(f"  复用已存在的当天{label}稿件，不覆盖")
            return

    items = []
    cp = BASE / "collected.json"
    if cp.exists():
        try:
            items = json.loads(cp.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"  读取 collected.json 失败: {e}")

    # 先把每条的摘要标准化，再连同标题一起批量译中（免费接口、带缓存）
    for it in items:
        it["summary"] = clean_summary(it.get("summary", ""), 200)
    try:
        import translate
        texts = []
        for it in items:
            texts += [it.get("title", ""), it.get("summary", "")]
        cmap = translate.translate_many(list(dict.fromkeys(t for t in texts if t)))
        n_tr = 0
        for it in items:
            for field in ("title", "summary"):
                orig = it.get(field, "")
                if orig and cmap.get(orig) and cmap[orig] != orig:
                    it[field] = cmap[orig]
                    n_tr += 1
        log(f"  英文标题/摘要译中：{n_tr} 处")
    except Exception as e:
        log(f"  翻译跳过：{e}")

    # 去重（标题前 14 字）
    seen, uniq = set(), []
    for it in items:
        t = clean(it.get("title", ""))
        key = t[:14]
        if not t or key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    def line(it, force_target=None):
        t = clean(it.get("title", ""))
        src = clean(it.get("source", ""), 24)
        main = f"- {t}（来源：{src}）。{impact(it, force_target)}"
        detail = detail_line(it, with_summary=True)
        return [x for x in (main, detail) if x]

    # 每条新闻只归入「最匹配的一个」栏目，避免重复刷屏
    buckets = {k: [] for k in ("global", "macro", "cmd", "oil", "cn", "cal")}
    for it in uniq:
        t = clean(it.get("title", ""))       # 分类只看标题
        blob = t + " " + clean(it.get("summary", ""), 80)
        if any(k in t for k in OIL_KW):
            buckets["oil"].append(line(it, force_target="油价"))
        elif any(k in t for k in MACRO_KW):
            buckets["macro"].append(line(it))
        elif any(k in t for k in CMD_KW):
            buckets["cmd"].append(line(it))
        elif any(k in t for k in CAL_KW) and len(buckets["cal"]) < 4:
            buckets["cal"].append(line(it))
        elif it.get("region") == "global":
            buckets["global"].append(line(it))
        else:
            buckets["cn"].append(line(it))

    # 今日重点：影响面 × 时效 × 信源多样性
    def score(it):
        blob = it.get("title", "") + " " + it.get("summary", "")
        v = 0
        if any(k in blob for k in BREADTH_MACRO):
            v += 6
        elif any(k in blob for k in BREADTH_SECTOR):
            v += 3
        if any(k in blob for k in BREADTH_COMPANY):
            v -= 5
        if it.get("region") == "global":
            v += 3
        age = float(it.get("age_hours") or 24)
        v += max(0.0, 3.0 - age / 6.0)
        return v

    ranked = sorted(uniq, key=score, reverse=True)
    focus, used_src, n_global = [], {}, 0
    for it in ranked:
        src = it.get("source", "")
        is_global = it.get("region") == "global"
        cap = 2 if is_global else 1
        if used_src.get(src, 0) >= cap or (is_global and n_global >= 3):
            continue
        focus.append(it)
        used_src[src] = used_src.get(src, 0) + 1
        n_global += 1 if is_global else 0
        if len(focus) >= 5:
            break

    focus_lines = []
    for x in focus:
        focus_lines.append(
            f"- {clean(x.get('title',''))}（来源：{clean(x.get('source',''),24)}）。{impact(x)}"
        )
        d = detail_line(x, with_summary=False)
        if d:
            focus_lines.append(d)

    out = [f"# 📈 {label} · {today}", "", "## 今日重点", ""]
    out += focus_lines or ["- 今日暂无重点快讯"]
    sections = [
        ("## 🌍 全球股市与美股", "global"),
        ("## 🏛 宏观与政策", "macro"),
        ("## 🛢 大宗商品", "cmd"),
        ("## 🛢 原油与能源", "oil"),
        ("## 🇨🇳 国内财经与 A股前瞻", "cn"),
        ("## 📅 今日重要数据/事件", "cal"),
    ]
    for title, key in sections:
        out += ["", title, ""]
        pairs = buckets[key][:5]
        flat = [ln for pair in pairs for ln in pair]
        out += flat or ["- 今日无重大相关消息"]

    md_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    n_detail = sum(1 for x in out if x.startswith("> "))
    log(f"  规则化生成{label}：{len(uniq)} 条（去重后），{n_detail} 条带摘要/原文链接")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("morning", "evening"), default=None)
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--skip-media", action="store_true")
    args = ap.parse_args()
    mode = args.mode or ("morning" if datetime.datetime.now().hour < 12 else "evening")

    log(f"===== 开始 {mode} 流水线 =====")
    os.chdir(BASE)

    run("collect.py", timeout=240)
    run("fetch_prices.py", timeout=240)
    try:
        build_briefing(mode)
    except Exception:
        log("  写稿异常: " + traceback.format_exc()[-400:])
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
        run("generate_audio.py", timeout=240)
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

    log(f"===== 结束 {mode} | 新闻 {n_news} 条 | 行情成功 {len(prices_ok)}/7 "
        f"({','.join(prices_ok) or '无'}) | 推送 {push_ok} =====")
    if push_ok is False:
        sys.exit(1)


if __name__ == "__main__":
    main()

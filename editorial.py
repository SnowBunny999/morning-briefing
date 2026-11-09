#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选稿 + 成稿引擎（纯规则，不依赖任何 LLM / 新密钥）。

两条硬标准（用户 2026-11-09 明确要求）：
  1. 只收「能挂到资产上」的新闻：A股/美股/股价/市值/汇率/黄金/利率/币价、
     央行与财政政策、宏观数据、大科技公司财报与动作。国际时事、政治外交、
     军事伤亡、社会案件、教程科普、营销标题、概念宣传一律剔除。
  2. 每条必须有一段「不点原文也能读懂整件事」的概括：从原文正文里抽取
     发生了什么 / 关键数字 / 背景原因 / 对资产的影响，2-3 句，不编造、
     不加「据消息人士」这类套话。概括不出来就不入选，宁缺毋滥。

概括句全部来自原文（先抓 article.py 正文，英文句再由 translate.py 译中），
不做任何生成式改写，所以不会凭空造事实。
"""
import re
from collections import defaultdict

import article

# ---------------------------------------------------------------- 词表
# 用户点名的可交易维度
ASSET = (
    "A股", "沪指", "上证", "深证", "创业板", "科创板", "沪深", "北向", "两市", "大盘",
    "美股", "标普", "纳指", "纳斯达克", "道指", "华尔街", "三大指数", "罗素",
    "港股", "恒生", "恒指", "日经", "欧股", "亚太股市",
    "股价", "市值", "估值", "市盈率", "市净率", "涨停", "跌停", "板块", "个股",
    "指数", "成交额", "成交量", "资金流", "主力资金", "北向资金", "融资融券",
    "ETF", "基金", "公募", "私募", "仓位", "加仓", "减仓", "认购", "赎回",
    "债券", "国债", "美债", "收益率", "利率", "降息", "加息", "降准", "议息",
    "联邦基金利率", "LPR", "MLF", "逆回购", "宽松", "收紧",
    "CPI", "PPI", "PMI", "非农", "失业率", "通胀", "通缩", "GDP", "零售销售",
    "经济数据", "初请失业金", "就业数据",
    "汇率", "人民币", "美元", "美元指数", "日元", "欧元", "外汇",
    "黄金", "金价", "白银", "贵金属", "XAU", "原油", "油价", "布伦特", "WTI",
    "OPEC", "欧佩克", "减产", "增产", "铜", "大宗商品", "期货",
    "比特币", "以太坊", "加密货币", "数字货币", "BTC", "ETH", "币价", "稳定币",
    "财报", "营收", "净利润", "业绩", "毛利", "回购", "分红", "派息", "增持", "减持",
    "IPO", "上市", "募资", "并购", "收购", "订单", "业绩指引", "盈利预期",
    "评级", "目标价", "现金储备", "营业利润",
)
POLICY_BODY = ("央行", "美联储", "联储", "欧洲央行", "日本央行", "人民银行", "证监会",
               "金融监管总局", "财政部", "发改委", "国务院", "白宫", "商务部", "SEC",
               "交易所", "监管机构", "政府")
POLICY_ACT = ("降息", "加息", "降准", "利率", "新规", "监管", "新政策", "关税",
              "制裁", "出口管制", "补贴", "减税", "税改", "刺激", "政策", "公布",
              "发布", "通过", "调整", "审批", "批准", "落地", "实施", "生效", "决议")
BIG_TECH = ("英伟达", "苹果", "微软", "Meta", "谷歌", "亚马逊", "特斯拉", "台积电",
            "中芯国际", "阿里巴巴", "腾讯", "伯克希尔", "OpenAI", "Anthropic",
            "AMD", "英特尔", "三星", "奈飞", "甲骨文", "华为", "小米", "比亚迪",
            "摩根大通", "高盛", "沃尔玛", "搜狐")
TECH_ACT = ("财报", "营收", "利润", "市值", "股价", "涨", "跌", "回购", "裁员",
            "投资", "建厂", "量产", "发布", "订单", "IPO", "收购", "拆分", "指引",
            "产能", "芯片", "数据中心", "业绩", "增资", "减持", "增持")

# 价格传导：地缘/政治类只有在写出明确链条时才放行
TRANSMIT = ("油价", "金价", "避险", "关税", "指数", "美元指数", "供应链", "减产",
            "出口管制", "市值", "股价", "涨", "跌", "收益率", "通胀", "汇率")

# 一票否决：与投资无关或低质内容
VETO_NEWS = re.compile(
    r"(空袭|袭击|伤亡|死亡|致死|导弹|战机|击落|开火|军事行动|爆炸|无人机|"
    r"弹劾|司法部|政党|竞选|谴责|斡旋|会谈|磋商|会晤|国事访问|严正声明|澄清|辟谣|"
    r"地震|洪水|火灾|坠机|事故|窃取|诈骗|起诉|诉讼|儿童安全|校园|种族|性别|"
    r"自民党|处分|党内|议员|外交|领事|峰会|共识|致辞|专访|颁奖|榜单|排行榜|"
    r"抗议|闯入|拘留|逮捕|示威|罢工|警方|警方表示)")
# 犯罪 / 社会 / 军事伤亡类：无条件剔除，金额里出现「美元」也不放行
VETO_HARD = re.compile(
    r"(窃取|偷窃|诈骗|起诉|诉讼|儿童安全|校园|种族|性别|"
    r"空袭|袭击|伤亡|死亡|致死|导弹|战机|击落|开火|"
    r"抗议|闯入|拘留|逮捕|示威|罢工|警方|判刑|监禁|"
    r"地震|洪水|火灾|坠机|事故)")
VETO_SOFT = re.compile(
    r"(能做什么|是什么|如何|怎么|指南|一览|手把手|教你|值得买吗|该不该买|"
    r"问答|科普|入门|盘点|一图看懂|拿去抄|抄作业|以下是|下面是|"
    r"今天.{0,8}发生了什么|发生了什么|一周.{0,4}回顾|Digest|"
    r"IRA|401\(k\)|遗产|退休金|我们\d{2,3}岁|"
    r"范式|全价值链|走进去|新篇章|高质量发展|赋能|凝聚共识|生态本土化|"
    r"大动作|最新表态|深夜|重磅|速看|惊人|炸裂|刚刚[！!]|突发[！!])")
VETO_PERSON = re.compile(r"(离职|加入|卸任|任命|出任|履新)")
# 快讯流里的「栏目型」条目：日程、汇总、复盘，不是一条完整新闻
VETO_META = re.compile(
    r"(^(提醒|提示|快讯|日内请重点关注|今日重点关注|重点关注|早间要闻汇总|"
    r"要闻汇总|早报|日报|周报|财经日历|今日(?:数据|日程|提醒)|整点回顾)|"
    r"(一览|速览|汇总|盘点)\s*[:：]?$)")
# 纯政治/国会新闻（「1.5万亿美元国防预算」这类不该进投资早报）
POLITICAL = re.compile(r"(国会|参议院|众议院|议员|法案|国防预算|弹劾|竞选|"
                       r"党团|中期选举改写|总统签署|行政令|外交|制裁声明)")
# 足以单独支撑一条投资新闻的强资产词；「美元 / 元 / 点」这类计价单位不算
STRONG_ASSET = (
    "A股", "沪指", "上证", "创业板", "科创板", "沪深", "北向", "美股", "标普",
    "纳指", "纳斯达克", "道指", "港股", "恒生", "股价", "市值", "估值", "市盈率",
    "涨停", "跌停", "大盘", "指数", "ETF", "基金", "北向资金", "债券", "国债",
    "美债", "收益率", "利率", "降息", "加息", "降准", "CPI", "PMI", "非农",
    "失业率", "通胀", "汇率", "人民币汇率", "美元指数", "黄金", "金价", "白银",
    "比特币", "以太坊", "加密货币", "原油", "油价", "OPEC", "减产", "财报",
    "营收", "净利润", "营业利润", "回购", "分红", "IPO", "业绩指引", "目标价",
)

NUM = re.compile(r"\d")
PRICE_NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|亿|万|美元|元|点|桶|股|倍|个基点|bp)")
FLUFF = re.compile(
    r"(据消息人士(?:透露|称|报道)?|据知情人士(?:透露|称)?|据报道|据外媒报道|"
    r"据(?:美联社|路透|彭博|法新社|共同社)[^，,]{0,8}(?:报道|消息)?|"
    r"据媒体(?:报道|称)?|据悉|据称|记者获悉|记者从[^，,]{0,12}了解到|有消息称|"
    r"^(?:人民财讯|财联社|证券时报|新华社)\s*\d{1,2}月\d{1,2}日电[，,]?\s*|"
    r"来源\s*[:：][^，,。]{0,20}[，,]?|点击(?:查看|阅读)|阅读(?:全文|更多)|订阅)")
# 正文按字数截断时常留下「…13时01分起」这类半截话
DANGLE_END = ("起", "的", "并", "及", "与", "和", "将", "把", "被", "经", "自",
              "在", "对", "为", "以", "于", "由", "受", "含", "即", "且", "或", "是")
# 纯行情报价播报（不是「影响市场的新闻」），同栏目限量保留
QUOTE_ONLY = re.compile(
    r"(开盘报|早盘收报|收盘报|收报\d|转跌|转涨|涨幅收窄|跌幅收窄|涨幅扩大|"
    r"隔夜SHIBOR|SHIBOR报|涨超\d|跌超\d|触及涨停|炸板率)")
SOFT_SENT = re.compile(
    r"(范式|全价值链|走进去|高质量发展|赋能|凝聚共识|新篇章|重要意义|"
    r"积极贡献|生动实践|引发关注|备受瞩目|值得期待|未来可期)")
CAUSE = ("因", "由于", "受", "此前", "此前已", "随着", "原因是", "背景", "去年",
         "同期", "今年", "此前", "源于", "在", "根据", "为应对", "此前宣布")
EFFECT = ("预计", "将", "或", "可能", "市场", "投资者", "股价", "分析师", "影响",
          "推动", "拖累", "利好", "利空", "下一步", "计划", "将于", "意味着",
          "导致", "提振", "压制", "有望", "令", "使得", "令", "引发")
DANGLE = ("原因是", "由于", "因为", "主要是", "源于", "包括", "以及", "并",
          "受", "其中", "从而", "进而", "导致", "伴随", "随着", "的", "是", "在", "对", "将")
INCOMPLETE = ("的", "因", "根据", "由于", "原因是", "并", "以及", "和", "与",
              "在", "对", "将", "包括", "是", "受", "把", "被", "为", "以", "及", "了")

SECTION_TITLE = {
    "us": "## 🌍 美股与全球市场",
    "macro": "## 🏛 央行 / 政策 / 宏观数据",
    "cmd": "## 🥇 黄金 · 数字货币 · 大宗商品",
    "oil": "## 🛢 原油与能源",
    "cn": "## 🇨🇳 A股与国内财经",
}
SECTION_ORDER = ("us", "macro", "cmd", "oil", "cn")
SECTION_RULES = (
    ("oil", ("原油", "油价", "OPEC", "欧佩克", "布伦特", "WTI", "产油", "减产",
             "增产", "页岩", "油气", "天然气", "伊拉克石油", "沙特")),
    ("cmd", ("黄金", "金价", "白银", "贵金属", "比特币", "以太坊", "加密货币",
             "数字货币", "BTC", "ETH", "铜", "大宗商品", "期货")),
    ("macro", ("央行", "美联储", "利率", "降息", "加息", "降准", "CPI", "PMI",
               "非农", "通胀", "关税", "国债", "收益率", "汇率", "人民币",
               "美元", "财政部", "证监会", "政策", "经济数据", "失业", "议息")),
    ("cn", ("A股", "沪指", "创业板", "上证", "深证", "沪深", "北向", "ETF",
            "基金", "证监会", "上市公司", "两市", "科创板", "人民币资产")),
)

OFFICIAL = ("美联储", "央行", "新华社", "AP", "Reuters", "Bloomberg", "财政部", "证监会")
MAINSTREAM = ("CNBC", "MarketWatch", "华尔街见闻", "财联社", "金十", "证券时报",
              "财新", "Cointelegraph", "Investing", "长城证券", "国金", "中信")
LOW_AUTHORITY = ("长城证券",)


def plain(s):
    s = re.sub(r"<[^>]*>", " ", s or "")
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)
    s = re.sub(r"https?://\S+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tighten(s):
    """去掉套话、来源署名、URL 和截断的尾巴，只留可核查的事实。"""
    s = plain(s)
    s = FLUFF.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ，,、；;。 ")
    parts = [p.strip() for p in re.split(r"[，,]", s) if p.strip()]
    while len(parts) > 1:
        last = parts[-1]
        if (last.endswith(INCOMPLETE) or len(last) < 6
                or (len(last) < 26 and any(k in last for k in DANGLE) and not NUM.search(last))
                or re.search(r"\d+(?:\.\d+)?\s*(?:亿|万)?美$", last)):
            parts.pop()
        else:
            break
    s = "，".join(parts).strip(" ，,、；;。 ")
    return re.sub(r"\s+", " ", s).strip()


def grams(s):
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s or "")
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def similar(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def info_score(s):
    v = 0.0
    v += min(4.0, len(NUM.findall(s)) * 1.4)
    if PRICE_NUM.search(s):
        v += 2.0
    if any(k in s for k in ASSET):
        v += 1.5
    if any(k in s for k in CAUSE):
        v += 0.8
    if any(k in s for k in EFFECT):
        v += 1.0
    if SOFT_SENT.search(s):
        v -= 3.0
    v -= abs(len(s) - 60) / 40.0
    return v


def split_sentences(text):
    """切句并清洗；保留英文句等待翻译，但不保留纯噪音。"""
    raw = article.sentences(plain(text), max_n=60)
    out = []
    for s in raw:
        s = tighten(s)
        if len(s) < 14 or SOFT_SENT.search(s):
            continue
        if s not in out:
            out.append(s)
    return out


def relevance(it):
    """硬门槛：必须能挂到资产上，且不相关新闻一律剔除。返回 (是否通过, 原因)。"""
    t = plain(it.get("title") or "")
    blob = t + " " + plain(it.get("summary") or "")[:220]
    hit_asset = [k for k in ASSET if k in blob]
    hit_policy = any(k in blob for k in POLICY_BODY) and any(k in blob for k in POLICY_ACT)
    hit_tech = any(k in blob for k in BIG_TECH) and any(k in blob for k in TECH_ACT)
    has_transmit = any(k in blob for k in TRANSMIT) and bool(NUM.search(blob))

    # 无关新闻：即使带市场词，也按主题否决（除非写出明确价格传导）
    if VETO_HARD.search(t):
        return False, "社会/犯罪/军事类"
    if VETO_NEWS.search(t) and not has_transmit:
        return False, "国际时事/政治社会类"
    if VETO_SOFT.search(t) and not (PRICE_NUM.search(t) and hit_asset):
        return False, "教程/营销/概念宣传"
    if VETO_META.search(t):
        return False, "日程/汇总栏目，非单条新闻"
    if VETO_PERSON.search(t) and not (PRICE_NUM.search(t) or any(
            k in t for k in ("财报", "业绩", "市值", "股价", "战略", "业务"))):
        return False, "纯人事变动"
    strong = any(k in blob for k in STRONG_ASSET)
    if POLITICAL.search(t) and not strong:
        return False, "政治/国会事务"

    if not (hit_asset or hit_policy or hit_tech or has_transmit):
        return False, "无资产/政策/公司维度"
    # 只靠「美元 / 元 / 点」这类计价单位，没有强资产词、也没有政策或公司动作 -> 剔除
    if hit_asset and not strong and not (hit_policy or hit_tech):
        return False, "仅计价单位，非投资事件"
    # 必须有可核查的实质信息：数字，或明确的政策/公司动作
    if not (NUM.search(blob) or hit_policy or hit_tech):
        return False, "无数字且无实质动作"
    return True, ("资产" if hit_asset else "政策" if hit_policy else "科技公司")


def score(it, body):
    blob = plain(it.get("title") or "") + " " + plain(it.get("summary") or "")[:200] + " " + body[:400]
    v, why = 0.0, []
    if any(k in blob for k in ("央行", "美联储", "利率", "降息", "加息", "CPI", "PMI",
                              "非农", "通胀", "汇率", "国债收益率")):
        v += 6
        why.append("宏观政策+6")
    if any(k in blob for k in ("标普", "纳指", "道指", "美股", "A股", "沪指", "港股",
                              "黄金", "金价", "比特币", "原油", "油价")):
        v += 5
        why.append("资产标的+5")
    if any(k in blob for k in BIG_TECH) and any(k in blob for k in TECH_ACT):
        v += 3
        why.append("大公司动作+3")
    if PRICE_NUM.search(blob):
        v += 2
        why.append("有具体数字+2")
    src = it.get("source", "")
    if any(k in src for k in OFFICIAL):
        v += 3
        why.append("权威源+3")
    elif any(k in src for k in MAINSTREAM):
        v += 1
    if any(k in src for k in LOW_AUTHORITY):
        v -= 2
    try:
        age = float(it.get("age_hours") or 0)
        if 0 <= age < 36:
            v += max(0.0, 3.0 - age / 12.0)
    except (TypeError, ValueError):
        pass
    return v, why


def candidate_sentences(it, limit=5):
    """短快讯的正文本身就是一段完整叙述，整段保留；长文章才挑关键句。"""
    title = plain(it.get("title") or "")
    tg = grams(title)
    body = plain(it.get("body") or "")
    summary = plain(it.get("summary") or "")
    text = body if len(body) >= len(summary) else summary
    ss = split_sentences(text)
    # 中文快讯 / 电报通常 60~250 字，逐句全用比「挑三句」更能讲清整件事
    if 0 < len(text) <= 320 and ss:
        return [s for s in ss[:4] if 14 <= len(s) <= 160]
    pool = ss[:40]
    scored = []
    for i, s in enumerate(pool):
        if len(s) > 150:
            s = s[:150].rstrip("，,、；;。 ")
        if similar(tg, grams(s)) >= 0.82:
            continue
        v = info_score(s) - i * 0.06
        scored.append((v, i, s))
    scored.sort(key=lambda x: -x[0])
    chosen = sorted(scored[:limit], key=lambda x: x[1])
    return [x[2] for x in chosen]


def summarize(title, sents):
    """从已译中的候选句里拼出 2-3 句完整概括；做不到就返回空列表（该条不入选）。"""
    tg = grams(plain(title))
    cand = []
    for s in sents:
        s = tighten(s)
        # 超长句多半是公告/法条全文，直接丢掉而不是截断成病句
        if len(s) < 14 or len(s) > 160 or s.endswith(DANGLE_END):
            continue
        if article.is_english(s) or SOFT_SENT.search(s):
            continue
        if similar(tg, grams(s)) >= 0.78:
            continue
        if any(similar(grams(s), grams(c)) >= 0.72 for c in cand):
            continue
        cand.append(s)
    if not cand:
        return []
    order = {s: i for i, s in enumerate(cand)}

    # 短快讯：候选句就是原文整段叙述，全部保留、保持原顺序
    if len(cand) <= 4 and sum(len(s) for s in cand) <= 330:
        para = list(cand)
    else:
        def pick(kws, used, prefer_num=False):
            pool = [(info_score(s) + (2 if prefer_num and PRICE_NUM.search(s) else 0)
                     + (1.5 if any(k in s for k in kws) else 0), s)
                    for s in cand if s not in used]
            return max(pool)[1] if pool else ""

        used = []
        para = []
        for kws, pnum in ((ASSET, True), (CAUSE, False), (EFFECT, False)):
            s = pick(kws, used, prefer_num=pnum)
            if s:
                used.append(s)
                para.append(s)
        para.sort(key=lambda s: order[s])
    if len("".join(para)) < 45:
        return []
    return para[:4]


def section_of(ev):
    blob = ev["title"] + " " + " ".join(ev["para"])[:120]
    for key, kws in SECTION_RULES:
        if any(k in blob for k in kws):
            return key
    return "us" if ev["raw"].get("region") == "global" else "cn"


def build_event(group, sents_map):
    lead = group[0]
    bodies = []
    for x in group:
        bodies += sents_map.get(id(x), [])
    para = summarize(lead.get("title", ""), bodies)
    if not para:
        return None
    sources, links = [], []
    for x in group:
        s = x.get("source", "")
        if s and s not in sources:
            sources.append(s)
        u = (x.get("link") or "").strip()
        if u.startswith("http") and u not in links:
            links.append(u)
    title = plain(lead.get("title") or "")
    title = re.sub(r"^[!！,，]+|[!！]+$", "", title).strip()
    title = re.sub(r"^(深夜|重磅|突发|刚刚|快讯|直播|图文|视频)\s*[丨|:：，,！!]?\s*", "", title)
    title = title.replace("！", "，").rstrip("，, ")
    if len(title) > 62:
        cut = title.find("。")
        title = title[:cut + 1] if 12 < cut < 62 else title[:62].rstrip("，, ")
    ev = {
        "title": title, "sources": sources, "links": links, "para": para,
        "score": max(x.get("_score", 0) for x in group),
        "raw": dict(lead),
    }
    ev["section"] = section_of(ev)
    return ev


def select(items, log=None, translate_fn=None, enrich_fn=None,
           target=12, limit=15, focus_n=3):
    """返回 (今日重点, 其余事件, 统计)。"""
    say = log or (lambda m: None)
    stats = {"raw": len(items), "offtopic": 0, "no_para": 0, "clusters": 0, "picked": 0}

    # 1) 去重 + 相关性硬门槛
    seen, prep = set(), []
    for it in items:
        t = plain(it.get("title") or "")
        key = t[:14]
        if not t or key in seen:
            continue
        seen.add(key)
        ok, _ = relevance(it)
        if not ok:
            stats["offtopic"] += 1
            continue
        s, why = score(it, plain(it.get("body") or it.get("summary") or ""))
        x = dict(it)
        x["_score"] = s
        x["_why"] = why
        x["_g"] = grams(t)
        prep.append(x)
    prep.sort(key=lambda x: -x["_score"])
    # 按信源配额保留，避免中文快讯流独占候选池，让 CNBC / Cointelegraph 也有机会
    quota, kept = defaultdict(int), []
    for x in prep:
        s = x.get("source", "")
        cap = 8 if any(k in s for k in LOW_AUTHORITY) else 14
        if quota[s] >= cap:
            continue
        quota[s] += 1
        kept.append(x)
    prep = kept[:60]

    # 2) 抓原文正文
    if enrich_fn:
        try:
            enrich_fn(prep, say)
        except Exception as e:
            say(f"  正文抓取跳过：{e}")

    # 3) 挑候选句 -> 批量译中 -> 回填
    sents_map = {}
    need_tr = []
    for x in prep:
        ss = candidate_sentences(x)
        sents_map[id(x)] = ss
        need_tr += [s for s in ss if article.is_english(s)]
    tr = {}
    if need_tr and translate_fn:
        try:
            tr = translate_fn(list(dict.fromkeys(need_tr))) or {}
        except Exception as e:
            say(f"  正文译中跳过：{e}")
    for x in prep:
        sents_map[id(x)] = [tr.get(s, s) for s in sents_map[id(x)]]

    # 4) 同事件聚类
    groups = []
    for x in prep:
        placed = False
        for g in groups:
            if any(similar(x["_g"], y["_g"]) >= 0.45 for y in g):
                g.append(x)
                placed = True
                break
        if not placed:
            groups.append([x])
    stats["clusters"] = len(groups)

    events = []
    for g in groups:
        ev = build_event(g, sents_map)
        if ev:
            events.append(ev)
        else:
            stats["no_para"] += 1
    events.sort(key=lambda e: -e["score"])

    # 5) 多样性：单源最多 5 条、同一家公司最多 2 条
    picked, per_src, ent_seen = [], defaultdict(int), defaultdict(int)
    n_quote = 0
    for e in events:
        src = e["sources"][0] if e["sources"] else ""
        if per_src[src] >= (2 if any(k in src for k in LOW_AUTHORITY) else 5):
            continue
        is_quote = bool(QUOTE_ONLY.search(e["title"]))
        if is_quote and n_quote >= 3:
            continue
        ents = [k for k in BIG_TECH if k in e["title"]]
        if any(ent_seen[k] >= 2 for k in ents):
            continue
        picked.append(e)
        per_src[src] += 1
        if is_quote:
            n_quote += 1
        for k in ents:
            ent_seen[k] += 1
        if len(picked) >= limit:
            break
    stats["picked"] = len(picked)
    say(f"  选稿：原始 {stats['raw']} / 非投资类 {stats['offtopic']} / 概括不足 {stats['no_para']} "
        f"/ 聚类 {stats['clusters']} / 采用 {stats['picked']} 条")
    for e in picked[:focus_n]:
        say(f"    [{e['score']:.1f}] {e['title'][:36]} | {' '.join(e['raw'].get('_why', [])[:2])}")
    return picked[:focus_n], picked[focus_n:], stats

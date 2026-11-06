#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日财经早报 Agent（云上定时版）
------------------------------------------------------------
流程：定时触发 -> 抓取 RSS 信源（隔夜美股/全球 + 国内前一工作日）
      -> 调 LLM 生成结构化早报 -> 推送到微信（Server酱）

依赖（见 requirements.txt）：feedparser, pyyaml, requests, openai
密钥通过环境变量传入（本地 .env / GitHub Secrets）：
  LLM_API_KEY    必填  LLM 的 API Key
  LLM_BASE_URL   选填  兼容 OpenAI 的接口地址，默认 DeepSeek
  LLM_MODEL      选填  模型名，默认 deepseek-chat
  SERVERCHAN_KEY 必填  Server酱 的 SENDKEY（微信推送）
"""

import os
import datetime
import yaml
import feedparser
import requests
import markdown
from openai import OpenAI
from dotenv import load_dotenv

# 读取项目根目录下的 .env（本地 / 计划任务运行时都靠它拿密钥）
load_dotenv()

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


# ----------------------------------------------------------
# 1) 配置
# ----------------------------------------------------------
def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ----------------------------------------------------------
# 2) 采集层：抓取单个 RSS，仅保留时间窗口内的条目
# ----------------------------------------------------------
# 部分站点（Google News、Investing 等）会拦截默认爬虫 UA，用浏览器 UA 才能拿到内容
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _build_headers(source: dict) -> dict:
    """合并浏览器 UA 与源自定义的请求头（部分站点如金十需要 Origin/Referer）"""
    h = {"User-Agent": UA}
    if source.get("headers"):
        h.update(source["headers"])
    return h


def fetch_feed(source: dict, hours_window: int) -> list:
    """RSS 源：拉取后交给 feedparser 解析"""
    name = source.get("name", "unknown")
    try:
        # 先用 requests 带超时拉取（避免个别源挂起导致整份早报卡死），再交给 feedparser 解析
        resp = requests.get(source["url"], headers=_build_headers(source), timeout=12)
        resp.encoding = resp.apparent_encoding or "utf-8"
        parsed = feedparser.parse(resp.text)
        if parsed.bozo and not parsed.entries:
            print(f"[warn] 解析失败或无内容：{name}")
            return []
        now = datetime.datetime.now(datetime.timezone.utc)
        items = []
        for e in parsed.entries:
            pub = e.get("published_parsed") or e.get("updated_parsed")
            age_hours = 9999.0
            if pub:
                pdt = datetime.datetime(*pub[:6], tzinfo=datetime.timezone.utc)
                age_hours = (now - pdt).total_seconds() / 3600.0
            # 没有时间字段的也保留，交给 LLM 判断；有时间的过滤窗口
            if age_hours <= hours_window or age_hours >= 9000:
                summary = (e.get("summary") or e.get("description") or "")
                # 去掉 HTML 标签的简单清理
                summary = summary.replace("\n", " ").strip()[:280]
                items.append({
                    "source": name,
                    "region": source.get("region", "global"),
                    "title": (e.get("title") or "").strip(),
                    "summary": summary,
                    "link": e.get("link", ""),
                    "age_hours": round(age_hours, 1),
                })
        print(f"[ok] {name}：{len(items)} 条（窗口 {hours_window}h）")
        return items
    except Exception as ex:
        print(f"[error] {name}：{ex}")
        return []


def fetch_json_source(source: dict, hours_window: int) -> list:
    """JSON API 源（如新浪滚动、金十电报、财联社电报）。字段名在 config.yaml 里配置。"""
    name = source.get("name", "unknown")
    try:
        resp = requests.get(source["url"], headers=_build_headers(source), timeout=12)
        resp.encoding = resp.apparent_encoding or "utf-8"
        data = resp.json()
        # 顺着 items_path 找到列表；若落在一个 dict 上，自动找里面第一个列表字段
        node = data
        for key in source["items_path"].split("."):
            node = node[key]
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
        link_field = source.get("link_field", "url")
        items = []
        for raw in node:
            if not isinstance(raw, dict):
                continue
            title = (raw.get(title_field) or "").strip()
            if not title:  # 回退：取第一个够长的文本字段当标题
                for v in raw.values():
                    if isinstance(v, str) and len(v) >= 6:
                        title = v.strip()
                        break
            summary = (raw.get(summary_field) if summary_field else "") or ""
            link = raw.get(link_field, "")
            age = 9999.0
            if tf and raw.get(tf) is not None:
                tv = raw[tf]
                if isinstance(tv, (int, float)):
                    ts = float(tv)
                    if ts > 1e12:  # 毫秒时间戳
                        ts /= 1000.0
                    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                    age = (now - dt).total_seconds() / 3600.0
                elif isinstance(tv, str) and tfmt:
                    try:
                        dt = datetime.datetime.strptime(tv, tfmt)
                        dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
                        age = (now - dt).total_seconds() / 3600.0
                    except Exception:
                        pass
            if age <= hours_window or age >= 9000:
                items.append({
                    "source": name,
                    "region": source.get("region", "global"),
                    "title": title,
                    "summary": summary.replace("\n", " ").strip()[:280],
                    "link": link,
                    "age_hours": round(age, 1),
                })
        print(f"[ok] {name}：{len(items)} 条（窗口 {hours_window}h）")
        return items
    except Exception as ex:
        print(f"[error] {name}：{ex}")
        return []


def fetch_source(source: dict, hours_window: int) -> list:
    """根据 type 分发到 RSS 或 JSON 抓取器"""
    if source.get("type") == "json":
        return fetch_json_source(source, hours_window)
    return fetch_feed(source, hours_window)


# ----------------------------------------------------------
# 3) 精选层：调 LLM 生成结构化早报
# ----------------------------------------------------------
def build_prompt(items: list, today: str) -> str:
    lines = []
    for it in items:
        lines.append(f"- [{it['region']}/{it['source']}] {it['title']}｜{it['summary']}")
    news_block = "\n".join(lines)
    return f"""你是一名资深财经编辑。以下是今天凌晨至早间，从多个可靠信源采集到的财经/市场/政策新闻原始条目（含美股、全球市场、国内政策）。
请整理成一份给 A 股个人投资者的**早报 Markdown**，要求：
1. 开头给「今日重点」3-5 条一句话摘要（最重要、最影响市场的）。
2. 分栏目组织：🌍 全球股市与美股 / 🏛 宏观与政策（美联储/利率/国内监管） / 🛢 大宗商品（原油/黄金/铜） / 🇨🇳 国内财经与 A股前瞻 / 📅 今日重要数据日历。
3. 每条新闻保留来源，用一句话写要点，并标注影响方向（利好/利空/中性）。
4. 去重、按重要性排序，总量控制在 20 条以内，语言简洁。
5. 不要编造未提供的信息，来源里没有的细节就省略。

日期：{today}
原始条目：
{news_block}
"""


def summarize(items: list, today: str) -> str:
    client = OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    )
    prompt = build_prompt(items, today)
    resp = client.chat.completions.create(
        model=os.environ.get("LLM_MODEL", "deepseek-chat"),
        messages=[
            {"role": "system",
             "content": "你是专业的财经新闻编辑，输出简洁、准确、结构化的中文早报，不编造信息。"},
            {"role": "user", "content": prompt},
        ],
        temperature=float(os.environ.get("LLM_TEMPERATURE", "0.3")),
    )
    return resp.choices[0].message.content


# ----------------------------------------------------------
# 3.5) 输出层：生成带「语音播报」按钮的 HTML 网页
# ----------------------------------------------------------
def render_html(markdown_text: str, date_str: str, audio: bool = False) -> str:
    """把 Markdown 早报渲染成自包含 HTML。

    - audio=True：嵌入预生成的 briefing.mp3（微软神经网络女声 XiaoxiaoNeural），
      全设备一致、音质自然；打开页面点一下顶部的「语音版」即播放。
    - audio=False（兜底）：退回浏览器原生语音合成（speechSynthesis），免费无需密钥。
    """
    body_html = markdown.markdown(
        markdown_text,
        extensions=["extra", "sane_lists", "nl2br"],
    )
    audio_tag = (
        '<audio id="player" src="briefing.mp3" preload="auto" controls '
        'style="width:100%;margin-top:14px"></audio>'
        if audio else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📈 财经早报 · {date_str}</title>
<style>
  :root {{ --bg:#0f1420; --card:#1a2233; --fg:#e8edf5; --muted:#9aa7bd; --accent:#4f8cff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         line-height:1.7; padding:56px 16px 40px; }}
  .bar {{ position:fixed; top:0; left:0; right:0; z-index:50;
          display:flex; align-items:center; gap:10px; justify-content:flex-end;
          padding:10px 14px; background:rgba(15,20,32,.92);
          backdrop-filter:blur(8px); border-bottom:1px solid #2a3450; }}
  .bar button {{ font-size:15px; padding:9px 16px; border:none; border-radius:999px;
                background:var(--accent); color:#fff; cursor:pointer; font-weight:600; }}
  .bar button:active {{ transform:scale(.96); }}
  .bar select {{ font-size:14px; padding:7px 8px; border-radius:8px;
                border:1px solid #2a3450; background:#13203a; color:var(--fg); }}
  .wrap {{ max-width:760px; margin:0 auto; }}
  .card {{ background:var(--card); border:1px solid #2a3450; border-radius:14px;
          padding:20px 22px; margin-bottom:18px; }}
  h1 {{ font-size:24px; margin:4px 0 12px; }}
  h2 {{ font-size:19px; margin:22px 0 8px; color:#cfe0ff; border-left:4px solid var(--accent);
       padding-left:10px; }}
  blockquote {{ margin:0 0 12px; padding:10px 14px; background:#13203a;
               border-left:4px solid #4f8cff; border-radius:8px; color:var(--muted); }}
  ul,ol {{ padding-left:22px; }}
  li {{ margin:6px 0; }}
  a {{ color:var(--accent); }}
  .foot {{ color:var(--muted); font-size:13px; text-align:center; margin-top:24px; }}
  code {{ background:#13203a; padding:1px 6px; border-radius:5px; }}
  .overlay {{ display:none; position:fixed; inset:0; z-index:100;
             flex-direction:column; align-items:center; justify-content:center; gap:18px;
             background:rgba(15,20,32,.97); backdrop-filter:blur(6px); text-align:center; padding:24px; }}
  .overlay h2 {{ border:none; padding:0; color:#e8edf5; font-size:21px; margin:0; }}
  .overlay button {{ font-size:21px; padding:18px 40px; border:none; border-radius:999px;
                     background:var(--accent); color:#fff; cursor:pointer; font-weight:700;
                     box-shadow:0 8px 30px rgba(79,140,255,.45); }}
  .overlay button:active {{ transform:scale(.97); }}
  .overlay small {{ color:var(--muted); font-size:13px; }}
</style>
</head>
<body>
  <div class="overlay" id="overlay">
    <button id="startBtn">语音版</button>
    <small>点一下即播 · 可连车载蓝牙</small>
  </div>
  <div class="bar">
    <select id="rate" title="朗读语速">
      <option value="0.85">0.85x</option>
      <option value="1" selected>1.0x</option>
      <option value="1.2">1.2x</option>
      <option value="1.4">1.4x</option>
    </select>
    <button id="playBtn">语音版</button>
  </div>
  <div class="wrap">
    <div class="card" id="content">
{body_html}
    </div>
{audio_tag}
    <div class="foot">语音由微软神经网络女声（XiaoxiaoNeural）朗读 · 建议连接车载蓝牙收听</div>
  </div>
<script>
  var btn = document.getElementById('playBtn');
  var rateSel = document.getElementById('rate');
  var overlay = document.getElementById('overlay');
  var startBtn = document.getElementById('startBtn');
  var player = document.getElementById('player');
  var useAudio = !!(player && player.src);

  if (useAudio) {{
    // —— 预生成 MP3 女声模式 ——
    btn.textContent = '语音版';
    function syncBtn() {{ btn.textContent = player.paused ? '语音版' : '⏸ 暂停'; }}
    btn.onclick = function() {{
      if (player.paused) {{ player.play(); }} else {{ player.pause(); }}
    }};
    player.onplay = syncBtn; player.onpause = syncBtn;
    rateSel.onchange = function() {{ player.playbackRate = parseFloat(rateSel.value); }};
    if (startBtn) startBtn.onclick = function() {{
      player.playbackRate = parseFloat(rateSel.value);
      try {{ player.play(); }} catch(e) {{}}
      overlay.style.display = 'none';
    }};
    window.addEventListener('load', function() {{
      player.playbackRate = parseFloat(rateSel.value);
      var p = player.play();
      if (p && p.catch) {{
        p.catch(function() {{
          setTimeout(function() {{ if (player.paused && overlay) overlay.style.display = 'flex'; }}, 600);
        }});
      }}
    }});
  }} else {{
    // —— 浏览器朗读兜底模式 ——
    var synth = window.speechSynthesis;
    var utter = null, playing = false;
    function pickVoice() {{
      var vs = synth.getVoices();
      for (var i=0;i<vs.length;i++) {{ if (/zh|cmn|Chinese/i.test(vs[i].lang + vs[i].name)) return vs[i]; }}
      return null;
    }}
    function start() {{
      var text = document.getElementById('content').innerText;
      utter = new SpeechSynthesisUtterance(text);
      utter.lang = 'zh-CN'; utter.rate = parseFloat(rateSel.value); utter.pitch = 1.0;
      var v = pickVoice(); if (v) utter.voice = v;
      utter.onend = function() {{ playing=false; btn.textContent='语音版'; }};
      utter.onerror = function() {{ playing=false; btn.textContent='语音版'; }};
      synth.cancel(); synth.speak(utter);
      playing = true; btn.textContent='⏸ 暂停';
    }}
    btn.onclick = function() {{
      if (!synth) {{ alert('当前浏览器不支持语音朗读，请用 Chrome / Edge / Safari 打开'); return; }}
      if (playing) {{ synth.pause(); playing=false; btn.textContent='▶️ 继续'; return; }}
      if (synth.paused && utter) {{ synth.resume(); playing=true; btn.textContent='⏸ 暂停'; return; }}
      start();
    }};
    function begin() {{
      if (!synth) {{ overlay.style.display='none'; return; }}
      try {{ start(); }} catch(e) {{}}
      overlay.style.display='none';
    }}
    if (startBtn) startBtn.onclick = begin;
    if (synth) synth.onvoiceschanged = function() {{ pickVoice(); }};
    window.addEventListener('load', function() {{
      if (synth) {{ try {{ start(); }} catch(e) {{}} }}
      setTimeout(function() {{ if (!playing && overlay) overlay.style.display='flex'; }}, 600);
    }});
  }}
</script>
</body>
</html>
"""


def save_html(markdown_text: str, date_str: str, path: str = "briefing.html"):
    html = render_html(markdown_text, date_str)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[ok] 已生成语音网页：{path}")
    return html


# ----------------------------------------------------------
# 4) 推送层：Server酱（微信）
# ----------------------------------------------------------
def push_to_wechat(title: str, content: str) -> dict:
    key = os.environ["SERVERCHAN_KEY"]
    url = f"https://sctapi.ftqq.com/{key}.send"
    r = requests.post(url, data={"title": title, "desp": content}, timeout=15)
    return r.json()


# ----------------------------------------------------------
# 3.7) 兜底：未配 LLM 密钥时，用原始采集条目直接生成网页（便于本地预览效果）
# ----------------------------------------------------------
def build_raw_markdown(items: list, today: str) -> str:
    cn = [i for i in items if i.get("region") == "cn"]
    gl = [i for i in items if i.get("region") != "cn"]
    lines = [
        f"# 📈 财经早报（原始采集 · 未 AI 摘要）· {today}",
        "",
        "> 提示：未配置有效 `LLM_API_KEY`，以下是原始采集条目。配置密钥后，"
        "AI 会把它整理成「今日重点 + 分栏目 + 利好/利空」的结构化早报。",
        "",
    ]
    if gl:
        lines.append("## 🌍 全球 / 美股")
        for i in gl:
            lines.append(f"- **{i['title']}**（{i['source']}）{i['summary']}")
    if cn:
        lines.append("## 🇨🇳 国内 / A股 / 政策")
        for i in cn:
            lines.append(f"- **{i['title']}**（{i['source']}）{i['summary']}")
    return "\n".join(lines)


# ----------------------------------------------------------
# 主流程
# ----------------------------------------------------------
def main():
    cfg = load_config()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    hours = int(cfg.get("time_window_hours", 18))
    max_items = int(cfg.get("max_items_for_llm", 60))

    all_items = []
    for src in cfg.get("sources", []):
        all_items += fetch_source(src, hours)

    if not all_items:
        print("未采集到任何条目，跳过本次推送。")
        return

    # 按时间倒序（新的在前），取前 N 条喂给 LLM，控制成本
    all_items.sort(key=lambda x: x.get("age_hours", 9999))
    top = all_items[:max_items]

    llm_key = os.environ.get("LLM_API_KEY", "")
    if not llm_key or llm_key.startswith("你的"):
        print("[warn] 未配置有效 LLM_API_KEY，使用原始采集条目生成（未做 AI 摘要）")
        markdown = build_raw_markdown(top, today)
    else:
        markdown = summarize(top, today)

    title = f"📈 财经早报 · {today}"

    # 1) 总是生成「可语音播放」的 HTML 网页（右上角播放键）
    save_html(markdown, today)

    # 2) 推送到微信；若配置了公开网页地址，在文末附上「点此听语音版」链接
    page_url = os.environ.get("BRIEFING_URL", "").strip()
    desp = markdown
    if page_url:
        desp += f"\n\n> 🔊 [点此在手机/电脑浏览器打开语音版（可朗读）]({page_url})"

    sc_key = os.environ.get("SERVERCHAN_KEY", "")
    if not sc_key or sc_key.startswith("你的"):
        # 没有配置推送密钥时，把早报打印出来，方便本地调试
        print("[warn] 未配置 SERVERCHAN_KEY，跳过微信推送（仅生成本地网页 briefing.html）")
        print("\n" + "=" * 60)
        print(title)
        print("=" * 60)
        print(markdown)
    else:
        result = push_to_wechat(title, desp)
        print("推送结果:", result)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取美股指数、黄金与主流数字货币行情，输出 prices.json（原始数据）与 prices_snippet.md（已排好版的早报片段）。
特点：
- 免费、无需任何 API 密钥；
- 多源容错（指数：Yahoo 主、stockanalysis 备；币价：OKX/Binance/MEXC/火币/Gate 依次；金价：gold-api/goldprice）；
- 即使某项全部源失败也会写出「获取失败，跳过」的说明，绝不阻塞流水线。
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def get_text(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", errors="ignore")


result = {"spx": None, "ixic": None, "gold": None, "btc": None,
          "eth": None, "oil_wti": None, "oil_brent": None,
          "usdcny": None, "errors": []}


# ---------- 美元兑人民币 ----------
def fetch_usdcny():
    try:
        d = get_json("https://api.frankfurter.app/latest?from=USD&to=CNY")
        return float(d["rates"]["CNY"])
    except Exception:
        d = get_json("https://open.er-api.com/v6/latest/USD")
        return float(d["rates"]["CNY"])


try:
    result["usdcny"] = fetch_usdcny()
except Exception as e:
    result["errors"].append("usdcny: " + str(e)[:80])


# ---------- 美股指数（Yahoo 主，stockanalysis 备） ----------
def _yahoo_idx(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol)
    d = get_json(url)
    meta = d["chart"]["result"][0]["meta"]
    price = float(meta["regularMarketPrice"])
    prev = float(meta.get("chartPreviousClose") or meta.get("previousClose"))
    return price, (price - prev) / prev * 100


def _sa_idx(sym):
    d = get_json(f"https://stockanalysis.com/api/symbol/quote/index/{sym}")
    if d.get("status") != 200:
        raise Exception("status=" + str(d.get("status")))
    p = d["data"]
    price = float(p["price"])
    chg = float(p.get("changePercentage") or p.get("changePercent") or 0)
    return price, chg


IDX_SRCS = {
    "spx": [(_yahoo_idx, "^GSPC"), (_sa_idx, "SPX")],
    "ixic": [(_yahoo_idx, "^IXIC"), (_sa_idx, "IXIC")],
}
for key, srcs in IDX_SRCS.items():
    val = chg = None
    for fn, arg in srcs:
        try:
            val, chg = fn(arg)
            break
        except Exception:
            continue
    if val is None:
        result["errors"].append("idx " + key + ": 全部源失败")
    else:
        result[key] = {"value": val, "chg_pct": chg}


# ---------- 黄金 XAU/USD ----------
def fetch_gold():
    try:
        g = get_json("https://api.gold-api.com/price/XAU")
        return {"usd_per_oz": float(g["price"]), "chg_pct": float(g.get("ch", 0) or 0)}
    except Exception:
        g = get_json("https://data-asg.goldprice.org/dbXRates/USD")
        return {"usd_per_oz": float(g["items"][0]["xauPrice"]), "chg_pct": 0.0}


try:
    result["gold"] = fetch_gold()
except Exception as e:
    result["errors"].append("gold: " + str(e)[:80])


# ---------- 数字货币（多交易所依次尝试） ----------
def _okx(inst):
    t = get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst}")
    last = float(t["data"][0]["last"]); open24 = float(t["data"][0]["open24h"])
    return last, (last - open24) / open24 * 100


def _binance(sym):
    t = get_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}")
    return float(t["lastPrice"]), float(t["priceChangePercent"])


def _mexc(sym):
    t = get_json(f"https://api.mexc.com/api/v3/ticker/24hr?symbol={sym}")
    return float(t["lastPrice"]), float(t["priceChangePercent"])


def _huobi(sym):
    t = get_json(f"https://api.huobi.pro/market/detail/merged?symbol={sym.lower()}")
    return float(t["tick"]["close"]), 0.0


def _gate(pair):
    arr = get_json(f"https://api.gateio.ws/api/v2/spot/ticker?currency_pair={pair}")
    obj = arr[0]
    return float(obj["last"]), float(obj.get("percent_change_value", 0) or 0)


SOURCES = {
    "btc-usdt": [(_okx, "BTC-USDT"), (_binance, "BTCUSDT"), (_mexc, "BTCUSDT"),
                 (_huobi, "btcusdt"), (_gate, "BTC_USDT")],
    "eth-usdt": [(_okx, "ETH-USDT"), (_binance, "ETHUSDT"), (_mexc, "ETHUSDT"),
                 (_huobi, "ethusdt"), (_gate, "ETH_USDT")],
}
for key, src_list in SOURCES.items():
    last = chg = None
    for fn, arg in src_list:
        try:
            last, chg = fn(arg)
            break
        except Exception:
            continue
    if last is None:
        result["errors"].append(key + ": 全部源失败")
    else:
        result[key] = {"usd": last, "chg_pct": chg}


# ---------- 原油 WTI / Brent（Yahoo 主；沙箱可能被拦，云端正常填值） ----------
def _yahoo_oil(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol)
    d = get_json(url)
    meta = d["chart"]["result"][0]["meta"]
    price = float(meta["regularMarketPrice"])
    prev = float(meta.get("chartPreviousClose") or meta.get("previousClose"))
    return price, (price - prev) / prev * 100


OIL_SRCS = {
    "oil_wti": "CL=F",      # WTI 原油期货（纽约轻质）
    "oil_brent": "BZ=F",    # 布伦特原油期货（北海）
}
for key, sym in OIL_SRCS.items():
    val = chg = None
    try:
        val, chg = _yahoo_oil(sym)
    except Exception:
        result["errors"].append("oil " + key + ": 源失败")
    if val is not None:
        result[key] = {"usd": val, "chg_pct": chg}

with open("prices.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

# ---------- 生成 markdown 片段 ----------
lines = ["## 💰 行情：美股指数 / 黄金 / 数字货币 / 原油", ""]
usdcny = result.get("usdcny")


def dir_tag(c, target):
    """行情方向带上具体对象：利好美股 / 利空黄金 / 利多油价 …"""
    if c is None or abs(c) < 0.005:
        return "中性"
    up = "利多" if target == "油价" else "利好"
    return ("%s%s" % (up, target)) if c >= 0 else ("利空%s" % target)


def fmt_pct(c):
    return "0.00%" if c is None else f"{c:+.2f}%"


# 美股指数
for label, key in (("标普500", "spx"), ("纳斯达克", "ixic")):
    d = result.get(key)
    if d:
        lines.append(f"- 📊 **{label}**：{d['value']:,.2f}，{fmt_pct(d['chg_pct'])}　*{dir_tag(d['chg_pct'], '美股')}*")
    else:
        lines.append(f"- 📊 **{label}**：行情获取失败，今日跳过")

# 黄金
g = result.get("gold")
if g:
    usd = g["usd_per_oz"]; chg = g["chg_pct"]
    cny_per_g = usd * usdcny / 31.1035 if usdcny else None
    cny_str = f"（≈ {cny_per_g:,.0f} 元/克）" if cny_per_g else ""
    lines.append(f"- 🥇 **黄金 XAU/USD**：{usd:,.0f} 美元/盎司{cny_str}，{fmt_pct(chg)}　*{dir_tag(chg, '黄金')}*")
else:
    lines.append("- 🥇 **黄金**：行情获取失败，今日跳过")

# 数字货币
for label, key, sym in (("比特币 BTC", "btc-usdt", "₿"), ("以太坊 ETH", "eth-usdt", "⟠")):
    d = result.get(key)
    if d:
        usd = d["usd"]; chg = d["chg_pct"]
        cny = usd * usdcny if usdcny else None
        cny_str = f"（≈ {cny:,.0f} 元）" if cny else ""
        lines.append(f"- {sym} **{label}**：{usd:,.0f} 美元{cny_str}，{fmt_pct(chg)}　*{dir_tag(chg, '币价')}*")
    else:
        lines.append(f"- {sym} **{label}**：行情获取失败，今日跳过")

# 原油
for label, key in (("WTI 原油", "oil_wti"), ("布伦特原油", "oil_brent")):
    d = result.get(key)
    if d:
        usd = d["usd"]; chg = d["chg_pct"]
        lines.append(f"- 🛢 **{label}**：{usd:,.2f} 美元/桶，{fmt_pct(chg)}　*{dir_tag(chg, '油价')}*")
    else:
        lines.append(f"- 🛢 **{label}**：行情获取失败，今日跳过")

lines.append("")
snippet = "\n".join(lines)
with open("prices_snippet.md", "w", encoding="utf-8") as f:
    f.write(snippet)

print(snippet)
print("\n--- 原始数据 ---")
print(json.dumps(result, ensure_ascii=False, indent=2))

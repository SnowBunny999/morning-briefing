#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 briefing.html：女声 MP3 + 逐条同步高亮 + 屏幕常亮。

时间轴 briefing.cues.json 会直接内嵌进 HTML（不单独发请求），
避免手机上因 sharecode 凭证丢失而 403。
没有 mp3 时退回浏览器朗读兜底，页面结构保持一致。
"""
import html as html_mod
import json
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from briefing_common import parse, render_cues  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))


def esc(s):
    return html_mod.escape(s, quote=True)


md = open(os.path.join(BASE, "briefing_today.md"), encoding="utf-8").read()
blocks = parse(md)
body = render_cues(blocks)

use_audio = os.path.exists(os.path.join(BASE, "briefing.mp3"))
try:
    with open(os.path.join(BASE, "briefing.cues.json"), encoding="utf-8") as f:
        cues = json.load(f) if use_audio else []
except Exception:
    cues = []

title = next((b["display"] for b in blocks if b["kind"] == "h1"), "财经早报")
today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

audio_tag = (
    '<audio id="player" src="briefing.mp3" preload="auto" autoplay playsinline '
    'webkit-playsinline controls style="width:100%;margin:14px 0 4px"></audio>'
    if use_audio else ""
)

TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>__TITLE__</title>
<style>
  :root { --bg:#0f1420; --card:#1a2233; --fg:#e8edf5; --muted:#9aa7bd; --accent:#4f8cff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         line-height:1.75; padding:60px 14px 90px; -webkit-text-size-adjust:100%; }
  .bar { position:fixed; top:0; left:0; right:0; z-index:50;
         display:flex; align-items:center; gap:8px; flex-wrap:wrap;
         padding:9px 12px calc(9px + env(safe-area-inset-top,0px));
         background:rgba(15,20,32,.95); backdrop-filter:blur(8px);
         border-bottom:1px solid #2a3450; }
  .bar button { font-size:15px; padding:9px 16px; border:none; border-radius:999px;
                background:var(--accent); color:#fff; cursor:pointer; font-weight:600; }
  .bar button:active { transform:scale(.96); }
  .bar select { font-size:14px; padding:7px 8px; border-radius:8px;
                border:1px solid #2a3450; background:#13203a; color:var(--fg); }
  .bar .state { font-size:12px; color:var(--muted); margin-left:auto; white-space:nowrap; }
  .bar .state.on { color:#6ee7a8; }
  .bar .toggle { font-size:12px; padding:6px 10px; background:#243354; }
  .bar .toggle.off { background:#3a3a44; color:var(--muted); }
  .wrap { max-width:760px; margin:0 auto; }
  .card { background:var(--card); border:1px solid #2a3450; border-radius:14px;
          padding:16px 18px; }
  .cue { border-radius:8px; padding:3px 9px; margin:2px -9px;
         transition:background .25s, box-shadow .25s, color .25s; }
  .cue.h1 { font-size:23px; font-weight:700; margin:2px 0 10px; }
  .cue.h2 { font-size:18px; font-weight:700; color:#cfe0ff; margin:20px 0 7px;
            border-left:4px solid var(--accent); border-radius:0 8px 8px 0; padding:4px 10px; }
  .cue.li { position:relative; padding-left:22px; }
  .cue.li:before { content:"•"; position:absolute; left:9px; color:var(--accent); }
  .cue.quote { color:var(--muted); background:#13203a; border-left:4px solid #4f8cff; }
  .cue.detail { color:var(--muted); font-size:.86em; line-height:1.6;
                background:#13203a; border-left:2px solid #33456b; border-radius:0 6px 6px 0;
                padding:5px 10px; margin:2px 0 9px 22px; }
  .cue.detail a { color:#8ab4ff; text-decoration:none; border-bottom:1px dotted #8ab4ff; }
  .cue.active { background:rgba(79,140,255,.22); box-shadow:inset 3px 0 0 var(--accent);
                color:#fff; }
  .src { color:var(--muted); font-size:.8em; opacity:.72; font-weight:400; }
  .foot { color:var(--muted); font-size:12px; text-align:center; margin-top:18px; }
  .overlay { display:none; position:fixed; inset:0; z-index:100;
             flex-direction:column; align-items:center; justify-content:center; gap:16px;
             background:rgba(15,20,32,.97); text-align:center; padding:24px; }
  .overlay button { font-size:22px; padding:20px 46px; border:none; border-radius:999px;
                    background:var(--accent); color:#fff; cursor:pointer; font-weight:700;
                    box-shadow:0 8px 30px rgba(79,140,255,.45); }
  .overlay small { color:var(--muted); font-size:13px; }
  a { color:var(--accent); }
</style>
</head>
<body>
  <div class="overlay" id="overlay">
    <button id="startBtn">语音版</button>
    <small>打开即播 · 若被浏览器拦住，点屏幕任意处即可 · 播放时屏幕保持常亮</small>
  </div>
  <div class="bar">
    <select id="rate" title="朗读语速">
      <option value="0.85">0.85x</option>
      <option value="1" selected>1.0x</option>
      <option value="1.2">1.2x</option>
      <option value="1.4">1.4x</option>
    </select>
    <button id="playBtn">语音版</button>
    <button id="followBtn" class="toggle" title="文字跟随朗读滚动">跟随：开</button>
    <span class="state" id="wakeState">屏幕常亮：待播放</span>
  </div>
  <div class="wrap">
    <div class="card" id="content">__BODY__</div>
    __AUDIO__
    <div class="foot">女声朗读 · 每条概括都会播报 · 来源 / 原文链接不播报 · 当前条目高亮并自动滚动</div>
  </div>
<script>
  var CUES = __CUES__;
  var player = document.getElementById('player');
  var btn = document.getElementById('playBtn');
  var rateSel = document.getElementById('rate');
  var overlay = document.getElementById('overlay');
  var startBtn = document.getElementById('startBtn');
  var wakeState = document.getElementById('wakeState');
  var followBtn = document.getElementById('followBtn');
  var nodes = document.querySelectorAll('.cue');
  var useAudio = !!player;
  var follow = true, lastIdx = -1, wakeLock = null, nosleep = null;

  // ---------- 屏幕常亮 ----------
  function setState(txt, on) {
    wakeState.textContent = '屏幕常亮：' + txt;
    wakeState.className = 'state' + (on ? ' on' : '');
  }
  async function keepAwake(on) {
    if (!on) {
      if (wakeLock) { try { wakeLock.release(); } catch (e) {} wakeLock = null; }
      if (nosleep) { try { nosleep.pause(); } catch (e) {} }
      setState('已关闭', false);
      return;
    }
    // 标准方案：Screen Wake Lock API（Android Chrome / Edge、Safari 16.4+）
    if ('wakeLock' in navigator) {
      try {
        if (!wakeLock) {
          wakeLock = await navigator.wakeLock.request('screen');
          wakeLock.addEventListener('release', function () { wakeLock = null; });
        }
        setState('已开启', true);
        return;
      } catch (e) { /* 落到兜底方案 */ }
    }
    // 兜底：iOS 老版本用 1px 静音视频循环阻止锁屏
    try {
      if (!nosleep) {
        var c = document.createElement('canvas'); c.width = c.height = 1;
        c.getContext('2d').fillRect(0, 0, 1, 1);
        nosleep = document.createElement('video');
        nosleep.setAttribute('playsinline', ''); nosleep.muted = true;
        nosleep.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
        nosleep.srcObject = c.captureStream(1);
        document.body.appendChild(nosleep);
      }
      nosleep.play().catch(function () {});
      setState('已开启(兼容)', true);
    } catch (e) { setState('浏览器不支持', false); }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      var playing = useAudio ? !player.paused : false;
      if (playing) keepAwake(true);
    }
  });

  // ---------- 文字同步高亮 ----------
  function highlight() {
    if (!CUES.length || !useAudio) return;
    var t = player.currentTime, idx = -1;
    for (var i = 0; i < CUES.length; i++) {
      if (t >= CUES[i].start && t < CUES[i].end) { idx = CUES[i].i; break; }
    }
    if (idx < 0 && t >= CUES[CUES.length - 1].end) idx = CUES[CUES.length - 1].i;
    if (idx === lastIdx) return;
    lastIdx = idx;
    for (var j = 0; j < nodes.length; j++) {
      nodes[j].classList.toggle('active', +nodes[j].dataset.i === idx);
    }
    if (follow && idx >= 0) {
      var el = document.querySelector('.cue.active');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }
  followBtn.onclick = function () {
    follow = !follow;
    followBtn.textContent = '跟随：' + (follow ? '开' : '关');
    followBtn.className = 'toggle' + (follow ? '' : ' off');
  };

  if (useAudio) {
    player.addEventListener('timeupdate', highlight);
    player.onplay = function () { btn.textContent = '⏸ 暂停'; keepAwake(true); };
    player.onpause = function () { btn.textContent = '语音版'; keepAwake(false); };
    player.onended = function () { btn.textContent = '语音版'; keepAwake(false); };
    btn.onclick = function () { player.paused ? player.play() : player.pause(); };
    rateSel.onchange = function () { player.playbackRate = parseFloat(rateSel.value); };
    if (startBtn) startBtn.onclick = function () {
      player.playbackRate = parseFloat(rateSel.value);
      try { player.play(); } catch (e) {}
      overlay.style.display = 'none'; keepAwake(true);
    };
    // 打开即播：微信 / iOS 对有声自动播放有系统限制，做三层兜底
    // ① 直接播 ② 微信 JSBridge 就绪回调里播 ③ 首次触屏/点击任意处播
    var started = false;
    function hideOverlay() { overlay.style.display = 'none'; }
    function tryPlay() {
      if (started) return;
      player.playbackRate = parseFloat(rateSel.value);
      var p;
      try { p = player.play(); } catch (e) { return; }
      if (p && p.then) {
        p.then(function () {
          started = true; hideOverlay(); keepAwake(true);
          document.removeEventListener('touchstart', onGesture);
          document.removeEventListener('click', onGesture);
        }).catch(function () {
          setTimeout(function () { if (!started) overlay.style.display = 'flex'; }, 600);
        });
      } else {
        started = true; hideOverlay(); keepAwake(true);
      }
    }
    function onGesture() { tryPlay(); }
    document.addEventListener('touchstart', onGesture, { passive: true });
    document.addEventListener('click', onGesture, { passive: true });
    overlay.addEventListener('touchstart', function (e) { e.stopPropagation(); tryPlay(); }, { passive: true });
    overlay.addEventListener('click', function (e) { e.stopPropagation(); tryPlay(); });
    window.addEventListener('load', function () {
      setTimeout(tryPlay, 100);
      if (typeof WeixinJSBridge !== 'undefined' && WeixinJSBridge.invoke) {
        WeixinJSBridge.invoke('getNetworkType', {}, function () { tryPlay(); });
      } else {
        document.addEventListener('WeixinJSBridgeReady', function () {
          try { WeixinJSBridge.invoke('getNetworkType', {}, function () { tryPlay(); }); } catch (e) {}
        });
      }
      setTimeout(function () { if (!started && player.paused) overlay.style.display = 'flex'; }, 900);
    });
  } else {
    // 兜底：浏览器自带朗读（无 mp3 时）
    var synth = window.speechSynthesis, utter = null, playing = false;
    function pickVoice() {
      var vs = synth.getVoices();
      for (var i = 0; i < vs.length; i++)
        if (/zh|cmn|Chinese/i.test(vs[i].lang + vs[i].name)) return vs[i];
      return null;
    }
    function start() {
      var lines = [];
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].classList.contains('detail')) continue;
        var clone = nodes[i].cloneNode(true);
        var s = clone.querySelector('.src'); if (s) s.remove();
        lines.push(clone.textContent.replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, '').trim());
      }
      utter = new SpeechSynthesisUtterance(lines.filter(Boolean).join('。'));
      utter.lang = 'zh-CN'; utter.rate = parseFloat(rateSel.value);
      var v = pickVoice(); if (v) utter.voice = v;
      utter.onend = utter.onerror = function () { playing = false; btn.textContent = '语音版'; keepAwake(false); };
      synth.cancel(); synth.speak(utter);
      playing = true; btn.textContent = '⏸ 暂停'; keepAwake(true);
    }
    btn.onclick = function () {
      if (!synth) { alert('当前浏览器不支持语音朗读'); return; }
      if (playing) { synth.pause(); playing = false; btn.textContent = '▶️ 继续'; keepAwake(false); return; }
      if (synth.paused && utter) { synth.resume(); playing = true; keepAwake(true); return; }
      start();
    };
    if (startBtn) startBtn.onclick = function () { try { start(); } catch (e) {} overlay.style.display = 'none'; };
    if (synth) synth.onvoiceschanged = pickVoice;
    window.addEventListener('load', function () {
      if (synth) { try { start(); } catch (e) {} }
      setTimeout(function () { if (!playing) overlay.style.display = 'flex'; }, 600);
    });
  }
</script>
</body>
</html>
"""

out = (TPL.replace("__TITLE__", esc(title))
          .replace("__BODY__", body)
          .replace("__AUDIO__", audio_tag)
          .replace("__CUES__", json.dumps(cues, ensure_ascii=False)))

path = os.path.join(BASE, "briefing.html")
with open(path, "w", encoding="utf-8") as f:
    f.write(out)

mode = "女声MP3 + 同步高亮 + 屏幕常亮" if use_audio else "浏览器朗读兜底"
n_read = sum(1 for b in blocks if b["speak"])
print(f"已生成 briefing.html（{len(out)} 字节，{len(blocks)} 个网页块 / {n_read} 个朗读块，"
      f"{len(cues)} 条时间轴，模式：{mode}）")

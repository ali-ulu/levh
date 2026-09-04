"""Librarian routes — bekçi ajanın durumu, taraması ve chat ekranı."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from server.core import librarian
from server.routes.models import AskRequest

router = APIRouter()


@router.get("/api/librarian/status")
async def librarian_status():
    return librarian.scan(store_memory=False)


@router.post("/api/librarian/scan")
async def librarian_scan():
    """Manuel tarama — bulguları hafızaya da yazar."""
    return librarian.scan(store_memory=True)


@router.post("/api/librarian/chat")
async def librarian_chat(req: AskRequest):
    return await librarian.chat(req.question)


@router.get("/librarian", response_class=HTMLResponse)
async def librarian_chat_page():
    return _CHAT_PAGE_HTML


@router.get("/librarian.js", response_class=HTMLResponse)
async def librarian_widget_js():
    """Dashboard'un her sayfasına enjekte edilen sağ-alt chat widget'ı."""
    return _WIDGET_JS


_WIDGET_JS = """(function () {
  if (window.__levhLibrarian) return;
  window.__levhLibrarian = true;
  var css = document.createElement('style');
  css.textContent = [
    '#lvw{position:fixed;right:20px;bottom:20px;width:340px;max-height:60vh;',
    'background:#1f2937;border:1px solid #374151;border-radius:12px;display:none;',
    'flex-direction:column;box-shadow:0 8px 30px rgba(0,0,0,.5);z-index:99999;',
    'font-family:system-ui,sans-serif;font-size:13px;color:#e5e7eb}',
    '#lvw.open{display:flex}',
    '#lvw-h{padding:10px 14px;background:#374151;border-radius:12px 12px 0 0;',
    'font-weight:600;cursor:pointer;display:flex;justify-content:space-between;align-items:center}',
    '#lvw-h .dot{width:10px;height:10px;border-radius:50%;background:#10b981;display:inline-block;margin-right:6px}',
    '#lvw-log{padding:10px;overflow-y:auto;flex:1;min-height:120px}',
    '#lvw-log .m{margin:6px 0;padding:8px 10px;border-radius:8px;white-space:pre-wrap}',
    '#lvw-log .me{background:#2563eb;margin-left:40px}',
    '#lvw-log .lib{background:#374151;margin-right:40px}',
    '#lvw-r{display:flex;border-top:1px solid #374151}',
    '#lvw-q{flex:1;background:#111827;color:#e5e7eb;border:0;padding:10px;outline:none;border-radius:0 0 0 12px}',
    '#lvw-b{background:#2563eb;color:#fff;border:0;padding:0 16px;cursor:pointer;border-radius:0 0 12px 0}',
    '#lvw-fab{position:fixed;right:20px;bottom:20px;width:52px;height:52px;border-radius:50%;',
    'background:#2563eb;color:#fff;border:0;font-size:22px;cursor:pointer;z-index:99999;',
    'box-shadow:0 4px 14px rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center}'
  ].join('');
  document.head.appendChild(css);

  var fab = document.createElement('button');
  fab.id = 'lvw-fab'; fab.textContent = '\\uD83D\\uDCDA';
  document.body.appendChild(fab);

  var w = document.createElement('div');
  w.id = 'lvw';
  w.innerHTML =
    '<div id="lvw-h"><span><span class="dot"></span>Librarian</span><small>\\u2715</small></div>' +
    '<div id="lvw-log"><div class="m lib">Merhaba! Hafiza kütüphanesinin memuruyum. Ne sormak istersin?</div></div>' +
    '<div id="lvw-r"><input id="lvw-q" placeholder="Soru yaz...">' +
    '<button id="lvw-b">G\\u00f6nder</button></div>';
  document.body.appendChild(w);

  function toggle() {
    var open = w.classList.toggle('open');
    fab.style.display = open ? 'none' : 'flex';
    if (open) document.getElementById('lvw-q').focus();
  }
  fab.onclick = toggle;
  document.getElementById('lvw-h').onclick = toggle;

  function send() {
    var q = document.getElementById('lvw-q');
    var v = q.value.trim(); if (!v) return;
    var log = document.getElementById('lvw-log');
    function add(t, cls) {
      var d = document.createElement('div');
      d.className = 'm ' + cls; d.textContent = t;
      log.appendChild(d); log.scrollTop = log.scrollHeight;
    }
    add(v, 'me'); q.value = '';
    fetch('/api/librarian/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: v })
    }).then(function (r) { return r.json(); })
      .then(function (j) { add(j.answer, 'lib'); })
      .catch(function (e) { add('Hata: ' + e, 'lib'); });
  }
  document.getElementById('lvw-b').onclick = send;
  document.getElementById('lvw-q').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') send();
  });
})();"""


_CHAT_PAGE_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>LEVH Librarian</title>
<style>
  body { font-family: system-ui, sans-serif; background:#111827; color:#e5e7eb; margin:0; }
  #widget { position:fixed; right:20px; bottom:20px; width:360px; max-height:70vh;
            background:#1f2937; border:1px solid #374151; border-radius:12px;
            display:flex; flex-direction:column; box-shadow:0 8px 30px rgba(0,0,0,.5); }
  #head { padding:10px 14px; background:#374151; border-radius:12px 12px 0 0;
          font-weight:600; display:flex; justify-content:space-between; align-items:center;}
  #head span.dot { width:10px; height:10px; border-radius:50%; background:#10b981; display:inline-block; margin-right:6px;}
  #log { padding:10px; overflow-y:auto; flex:1; font-size:13px; }
  .msg { margin:6px 0; padding:8px 10px; border-radius:8px; white-space:pre-wrap; }
  .me { background:#2563eb; margin-left:40px; }
  .lib { background:#374151; margin-right:40px; }
  #row { display:flex; border-top:1px solid #374151; }
  #q { flex:1; background:#111827; color:#e5e7eb; border:0; padding:10px; outline:none;
       border-radius:0 0 0 12px; }
  button { background:#2563eb; color:#fff; border:0; padding:0 16px; cursor:pointer;
           border-radius:0 0 12px 0; }
</style>
</head>
<body>
<h2 style="padding:20px">LEVH — Librarian bekçi ajanı</h2>
<p style="padding:0 20px;color:#9ca3af">Sağ alttaki sohbet penceresinden hafızayla ilgili her şeyi sorabilirsin. Durum: <a style="color:#60a5fa" href="/api/librarian/status">/api/librarian/status</a></p>
<div id="widget">
  <div id="head"><span><span class="dot"></span>Librarian</span><small>LEVH</small></div>
  <div id="log"><div class="msg lib">Merhaba! Hafıza kütüphanesinin memuruyum. Ne sormak istersin?</div></div>
  <div id="row">
    <input id="q" placeholder="Soru yaz..." onkeydown="if(event.key==='Enter')send()">
    <button onclick="send()">Gönder</button>
  </div>
</div>
<script>
const log = document.getElementById('log');
function add(t, cls){ const d=document.createElement('div'); d.className='msg '+cls;
  d.textContent=t; log.appendChild(d); log.scrollTop=log.scrollHeight; }
async function send(){
  const inp=document.getElementById('q'); const q=inp.value.trim(); if(!q)return;
  add(q,'me'); inp.value='';
  try{
    const r = await fetch('/api/librarian/chat',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question:q})});
    const j = await r.json();
    add(j.answer,'lib');
  }catch(e){ add('Hata: '+e,'lib'); }
}
</script>
</body>
</html>"""
"""SC2 live arena with LLM commanders — watch AI vs AI, on the map and in text.

Two agents (not bots) each get their own LLM policy, so this is genuinely
LLM vs LLM. The rendered RGB frame is served as MJPEG while /state exposes what
each commander is currently doing, so you can see decisions and consequences.

Run:  ./run_sc2.sh .venv311/bin/python live_viewer.py [map] [port]
Open: http://127.0.0.1:8099/
"""
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from absl import app
from PIL import Image

from arena.raw_engine import RawArena
from arena.commander import Commander, ScriptedPlanner
from arena.llm import LLMClient, PROVIDERS, available_providers

MAP = "Acropolis"
PORT = 8099
JPEG_QUALITY = 75
DECIDE_EVERY = 6           # game seconds; Commander default cadence

_lock = threading.Lock()
_stop = threading.Event()
_state = {
    "jpeg": None,
    "frame_id": 0,
    "game_loop": 0,
    "fps": 0.0,
    "status": "starting",
    "error": None,
    "started": time.time(),
    "players": [],          # [{name, provider, model, skill, stats, log}]
    "warnings": [],
}


def _provider_setup():
    """Pick provider from ARENA_PROVIDER, falling back to mock with a warning."""
    warnings = []
    want = os.environ.get("ARENA_PROVIDER", "deepseek").strip().lower()
    if want not in PROVIDERS:
        warnings.append("unknown ARENA_PROVIDER=%r, using mock" % want)
        want = "mock"
    p = PROVIDERS[want]
    if not p.configured:
        warnings.append(
            "%s has no %s — running MOCK commanders. "
            "export %s=sk-... and restart to use the real model."
            % (want, p.key_env, p.key_env))
        want = "mock"
    return want, warnings


def game_loop():
    commanders=[]
    try:
        provider, warnings = _provider_setup()
        names=[provider,os.environ.get("ARENA_P2_PROVIDER",provider)]
        clients=[]
        for name in names:
            if name not in PROVIDERS or (name!="mock" and not PROVIDERS[name].configured):
                raise ValueError("unknown/unconfigured provider "+name)
            clients.append(ScriptedPlanner(float(os.environ.get("ARENA_DELAY","0")))
                           if name=="mock" else LLMClient(name))
        with _lock:
            _state["warnings"]=warnings
            _state["status"]="launching realtime SC2"
        with RawArena(MAP,render=True) as arena:
            commanders=[Commander(arena,i,c) for i,c in enumerate(clients)]
            worlds=arena.initial
            with _lock:
                _state["players"]=[{"name":"P%d"%(i+1),"provider":name,
                    "model":"scripted-baseline" if name=="mock" else clients[i].model}
                    for i,name in enumerate(names)]
                _state["status"]="running (realtime=True)"
            while not _stop.is_set() and not arena.ended:
                worlds=arena.step([c.tick(w) for c,w in zip(commanders,worlds)])
                arr=np.asarray(arena.rgb()).astype(np.uint8)
                buf=io.BytesIO()
                Image.fromarray(arr).save(buf,format="JPEG",quality=JPEG_QUALITY)
                with _lock:
                    _state["jpeg"]=buf.getvalue()
                    _state["frame_id"]+=1
                    _state["game_loop"]=worlds[0].game_loop
                    _state["timing"]=arena.clock.report()
                    _state["fps"]=round(_state["timing"]["fps"],1)
                    for info,c,w in zip(_state["players"],commanders,worlds):
                        stats=c.stats()
                        info["skill"]=", ".join(t["skill"] for t in stats["active_tasks"])
                        info["stats"]=stats
                        info["summary"]=json.dumps(w.summary())
                        info["log"]=c.log[-6:]
            with _lock:
                _state["status"]="finished" if arena.ended else "stopped"
                _state["results"]=arena.result()
    except Exception as e:
        import traceback
        with _lock:
            _state["status"]="ERROR"
            _state["error"]="%s: %s" % (type(e).__name__,e)
        traceback.print_exc()
    finally:
        for c in commanders: c.stop()


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>SC2 LLM Arena</title>
<style>
 body{margin:0;background:#0b0e14;color:#c9d1d9;
      font:13px/1.45 ui-monospace,SFMono-Regular,monospace}
 header{padding:8px 14px;background:#111823;border-bottom:1px solid #1f2a37;
        display:flex;gap:18px;align-items:center;flex-wrap:wrap}
 h1{font-size:14px;margin:0;color:#58a6ff;font-weight:600}
 .k{color:#6e7d91}.v{color:#7ee787}
 .warn{background:#3d1d1d;border:1px solid #8b3a3a;color:#ffb4b4;
       padding:8px 14px;margin:8px 14px;border-radius:4px}
 main{display:flex;gap:14px;padding:14px;align-items:flex-start}
 .map{flex:0 0 auto}
 img{display:block;width:576px;height:432px;image-rendering:auto;
     background:#000;border:1px solid #1f2a37}
 .col{flex:1;min-width:0}
 .card{background:#0f1620;border:1px solid #1f2a37;border-radius:5px;
       padding:9px 11px;margin-bottom:10px}
 .card h2{font-size:12px;margin:0 0 7px;color:#58a6ff}
 .pill{display:inline-block;background:#1f2a37;border-radius:3px;
       padding:1px 6px;margin-right:5px;color:#a5d6ff}
 .skill{color:#ffa657;font-weight:600}
 .log{color:#8b949e;white-space:pre-wrap;word-break:break-word}
 .log div{padding:1px 0;border-bottom:1px dotted #161b22}
 .dim{color:#54606e}
</style></head>
<body>
<header>
 <h1>SC2 LLM Arena</h1>
 <span><span class="k">map</span> <span class="v">@@MAP@@</span></span>
 <span><span class="k">loop</span> <span class="v" id="gl">-</span></span>
 <span><span class="k">fps</span> <span class="v" id="fps">-</span></span>
 <span><span class="k">status</span> <span class="v" id="st">-</span></span>
 <span><span class="k">provider</span> <span class="v" id="pv">-</span></span>
 <span><span class="k">decide every</span> <span class="v">@@DECIDE@@ game seconds</span></span>
</header>
<div id="warns"></div>
<main>
 <div class="map"><img src="/stream"></div>
 <div class="col" id="players"></div>
</main>
<script>
async function tick(){
  try{
    const r = await fetch('/state'); const j = await r.json();
    document.getElementById('gl').textContent = j.game_loop;
    document.getElementById('fps').textContent = j.fps;
    document.getElementById('st').textContent = j.status;
    if(j.players && j.players.length)
      document.getElementById('pv').textContent =
        j.players.map(p=>p.provider+'/'+p.model).join('  vs  ');
    const wr = document.getElementById('warns');
    wr.innerHTML = (j.warnings||[]).map(w=>'<div class="warn">⚠ '+w+'</div>').join('');
    const box = document.getElementById('players');
    box.innerHTML = (j.players||[]).map(p=>{
      const s = p.stats||{};
      return '<div class="card"><h2>'+p.name+' &nbsp;<span class="dim">'
        +p.provider+' / '+p.model+'</span></h2>'
        +'<div><span class="pill">skill</span><span class="skill">'
        +(p.skill||'-')+'</span></div>'
        +'<div class="dim" style="margin:5px 0">'+(p.summary||'')+'</div>'
        +'<div><span class="pill">decisions</span>'+(s.decisions??'-')
        +' <span class="pill">llm errors</span>'+(s.errors??'-')
        +' <span class="pill">queued</span>'+(s.active_tasks?.length??'-')+'</div>'
        +'<div class="log" style="margin-top:7px">'
        +(p.log||[]).map(l=>'<div>'+l.replace(/</g,'&lt;')+'</div>').join('')
        +'</div></div>';
    }).join('');
  }catch(e){}
}
setInterval(tick, 1000); tick();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/stream"):
            self._stream()
        elif self.path.startswith("/state"):
            self._json()
        elif self.path.startswith("/frame"):
            self._frame()
        else:
            # NOTE: do NOT use str.format here -- the page contains lots of CSS/JS
            # braces and format() treats them as fields (KeyError: 'margin').
            body = (PAGE.replace("@@MAP@@", MAP)
                        .replace("@@DECIDE@@", str(DECIDE_EVERY))
                        .encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _json(self):
        with _lock:
            payload = {k: _state[k] for k in
                       ("frame_id", "game_loop", "fps", "status", "error",
                        "players", "warnings")}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _frame(self):
        with _lock:
            jpeg = _state["jpeg"]
        if not jpeg:
            self.send_error(503, "no frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        last = -1
        while not _stop.is_set():
            with _lock:
                jpeg = _state["jpeg"]
                fid = _state["frame_id"]
            if jpeg is None or fid == last:
                time.sleep(0.01)
                continue
            last = fid
            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(
                    ("Content-Length: %d\r\n\r\n" % len(jpeg)).encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return


def main(argv):
    global MAP, PORT
    if len(argv) > 1:
        MAP = argv[1]
    if len(argv) > 2:
        PORT = int(argv[2])

    print("provider availability:")
    for n, s in sorted(available_providers().items()):
        print("  %-10s %s" % (n, s))
    print()

    threading.Thread(target=game_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 60)
    print("  SC2 LLM Arena  ->  http://127.0.0.1:%d/" % PORT)
    print("  map: %s   plan every %d game seconds; realtime=True" % (MAP, DECIDE_EVERY))
    print("=" * 60, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        srv.shutdown()


if __name__ == "__main__":
    app.run(main)

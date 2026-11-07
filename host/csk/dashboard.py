"""Tiny read-only web dashboard for the 24/7 monitor.

Lets you glance at the Flip's monitor from your phone on the same network: live
alerts and the device inventory, auto-refreshing. Read-only - it never accepts
commands, so exposing it on the LAN can't be used to control the kit.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bleparse import HIGH, INFO, WARN

LEVEL_NAME = {INFO: "info", WARN: "warn", HIGH: "high"}


def build_state(world, store=None, scan=None) -> dict:
    now = world.clock()
    alerts = [{"level": LEVEL_NAME.get(a.level, "info"), "title": a.title, "detail": a.detail,
               "age": int(now - a.last), "active": a.active(now)}
              for a in world.sorted_alerts()]
    devs = []
    for d in sorted(world.devices.values(), key=lambda d: -d.last):
        if now - d.last > 300:
            continue
        devs.append({"kind": d.kind, "addr": d.addr, "title": d.title(), "rssi": d.rssi,
                     "category": d.category, "vendor": d.vendor, "age": int(now - d.last),
                     "mine": world.is_allowed(d.id)})
    out = {"ts": now, "location": world.location, "sensor_ok": world.sensor_ok(),
           "sensor": world.sensor_hello, "counts": {"devices": len(devs), "alerts": len(alerts)},
           "alerts": alerts, "devices": devs[:200]}
    if scan is not None:
        out["lan"] = scan
    return out


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>THE UNKILLABLE DEMON KING</title><style>
*{box-sizing:border-box}body{margin:0;background:#090a0e;color:#e9edf3;font:15px system-ui,sans-serif}
header{background:#10121a;padding:12px 16px;position:sticky;top:0;border-bottom:1px solid #262a34;display:flex;gap:12px;align-items:center}
h1{font-size:16px;margin:0;color:#ff565c;letter-spacing:1px}.dot{width:10px;height:10px;border-radius:50%}
.wrap{padding:12px 16px;max-width:760px;margin:auto}.card{background:#161922;border:1px solid #262a34;border-radius:12px;padding:10px 12px;margin:8px 0}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;color:#090a0e}
.high{background:#ff565c}.warn{background:#ffb73b}.info{background:#78bee6}
.t{font-weight:700}.d{color:#80889a;font-size:13px}.r{float:right;color:#80889a;font-size:12px}
h2{font-size:13px;color:#80889a;text-transform:uppercase;letter-spacing:1px;margin:18px 0 4px}
small{color:#5a6070}</style></head><body>
<header><div class=dot id=dot></div><h1>DEMON KING</h1><span class=d id=sub></span></header>
<div class=wrap><h2>Alerts</h2><div id=alerts></div><h2>Devices</h2><div id=devs></div>
<small id=foot></small></div>
<script>
async function tick(){
 try{const s=await (await fetch('/api/state')).json();
 document.getElementById('dot').style.background=s.sensor_ok?'#48d68a':'#ff565c';
 document.getElementById('sub').textContent='@'+s.location+'  '+s.counts.devices+' devices  '+s.counts.alerts+' alerts';
 document.getElementById('alerts').innerHTML=s.alerts.length?s.alerts.map(a=>
   `<div class=card><span class="badge ${a.level}">${a.level.toUpperCase()}</span> <span class=t>${esc(a.title)}</span>
    <span class=r>${a.active?'now':a.age+'s ago'}</span><div class=d>${esc(a.detail)}</div></div>`).join('')
   :'<div class=card><span class=d>No alerts. Watching...</span></div>';
 document.getElementById('devs').innerHTML=s.devices.map(d=>
   `<div class=card><span class=t>${esc(d.title)}</span>${d.mine?' <span class="badge info">MINE</span>':''}
    <span class=r>${d.rssi} dBm · ${d.age}s</span><div class=d>${d.kind} ${esc(d.addr)} ${esc(d.category||'')}</div></div>`).join('');
 document.getElementById('foot').textContent='updated '+new Date().toLocaleTimeString();
 }catch(e){document.getElementById('dot').style.background='#ff565c';}
}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
tick();setInterval(tick,3000);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            try:
                data = json.dumps(self.server.get_state()).encode()
            except Exception as e:
                data = json.dumps({"error": str(e)}).encode()
            self._send(200, data, "application/json")
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")


class Dashboard:
    def __init__(self, get_state, host="0.0.0.0", port=8099):
        self.get_state = get_state
        self.host, self.port = host, port
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> "Dashboard":
        self.httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self.httpd.get_state = self.get_state
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()

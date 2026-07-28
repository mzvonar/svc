#!/usr/bin/env python3
"""svc-web — tiny zero-dependency web UI over `svc`.

Normally socket-activated by systemd (svc-web.socket on 127.0.0.1:9099), so it
consumes nothing while idle and exits again after IDLE_EXIT seconds without
requests. Can also run standalone: svc-web.py --port 9099
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SVC = os.path.expanduser("~/.local/bin/svc")
IDLE_EXIT = 600
_last_request = time.time()


def run(args, cwd=None):
    r = subprocess.run(args, text=True, capture_output=True, cwd=cwd)
    return r.returncode, (r.stdout + r.stderr).strip()


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>svc</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { color-scheme: dark; }
body { font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
       background:#14161a; color:#d6d8dc; margin:2rem auto; max-width:70rem; padding:0 1rem; }
h1 { font-size:1.1rem; } h1 span { color:#6b7280; font-weight:normal; }
table { border-collapse:collapse; width:100%; margin:1rem 0; }
th, td { text-align:left; padding:.35rem .7rem; border-bottom:1px solid #262a31; white-space:nowrap; }
th { color:#8b919a; font-weight:normal; }
tr.stopped td { color:#6b7280; }
.state-running { color:#4ade80; } .state-failed { color:#f87171; } .dead { color:#fbbf24; }
button { background:#262a31; color:#d6d8dc; border:1px solid #3a3f47; border-radius:4px;
         padding:.15rem .6rem; margin-right:.3rem; cursor:pointer; font:inherit; font-size:12px; }
button:hover { background:#3a3f47; }
pre { background:#0d0f12; border:1px solid #262a31; border-radius:6px; padding:1rem;
      overflow-x:auto; max-height:28rem; white-space:pre-wrap; }
#msg { color:#8b919a; min-height:1.2rem; }
</style></head><body>
<h1>svc <span id="host"></span> <button onclick="act({op:'gc'})">gc</button></h1>
<div id="msg"></div>
<table><thead><tr><th>service</th><th>instance</th><th>kind</th><th>state</th>
<th>port</th><th>pid</th><th>owner</th><th></th></tr></thead><tbody id="rows"></tbody></table>
<pre id="logs" hidden></pre>
<script>
const el = id => document.getElementById(id);
async function refresh() {
  const rows = await (await fetch('api/units')).json();
  el('rows').innerHTML = rows.map(r => {
    const stateCls = r.state.startsWith('active') ? 'state-running'
                   : r.state.startsWith('failed') ? 'state-failed' : '';
    const owner = r.owner ? (r.owner + (r.owner_alive ? '' : ' <span class="dead">dead</span>')) : '-';
    const running = r.state !== 'stopped';
    const btns = [
      running ? `<button onclick='act({op:"stop",unit:${JSON.stringify(r.unit)}})'>stop</button>` : '',
      running && r.kind === 'service'
        ? `<button onclick='act({op:"restart",service:${JSON.stringify(r.service)},wt:${JSON.stringify(r.wt)}})'>restart</button>` : '',
      !running ? `<button onclick='act({op:"up",service:${JSON.stringify(r.service)},wt:${JSON.stringify(r.wt)}})'>start</button>` : '',
      running ? `<button onclick='logs(${JSON.stringify(r.unit)})'>logs</button>` : '',
    ].join('');
    return `<tr class="${running ? '' : 'stopped'}"><td>${r.service}</td><td>${r.slug}</td>` +
      `<td>${r.kind}</td><td class="${stateCls}">${r.state}</td><td>${r.port || '-'}</td>` +
      `<td>${r.pid || '-'}</td><td>${owner}</td><td>${btns}</td></tr>`;
  }).join('');
}
async function act(body) {
  el('msg').textContent = body.op + '...';
  const r = await fetch('api/action', {method:'POST', body: JSON.stringify(body)});
  el('msg').textContent = (await r.text()).slice(0, 300);
  refresh();
}
async function logs(unit) {
  const t = await (await fetch('api/logs?unit=' + encodeURIComponent(unit))).text();
  el('logs').hidden = false;
  el('logs').textContent = t || '(no output)';
}
el('host').textContent = '@ ' + location.hostname;
refresh();
setInterval(refresh, 3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/plain", code=200):
        global _last_request
        _last_request = time.time()
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(PAGE, "text/html")
        elif url.path == "/api/units":
            _, out = run([SVC, "status", "--json"])
            self._send(out or "[]", "application/json")
        elif url.path == "/api/logs":
            unit = parse_qs(url.query).get("unit", [""])[0]
            if not unit.startswith("svc-"):
                return self._send("bad unit", code=400)
            _, out = run(["journalctl", "--user", "-u", unit, "-n", "300",
                          "--no-pager", "-o", "short-iso"])
            self._send(out)
        else:
            self._send("not found", code=404)

    def do_POST(self):
        if self.path != "/api/action":
            return self._send("not found", code=404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (ValueError, TypeError):
            return self._send("bad request", code=400)
        op = body.get("op")
        if op == "gc":
            _, out = run([SVC, "gc"])
        elif op == "stop" and str(body.get("unit", "")).startswith("svc-"):
            _, out = run([SVC, "stop", body["unit"]])
        elif op in ("up", "restart") and body.get("service") and body.get("wt"):
            if not os.path.isdir(body["wt"]):
                return self._send("worktree gone", code=400)
            _, out = run([SVC, op, body["service"]], cwd=body["wt"])
        else:
            return self._send("bad action", code=400)
        self._send(out or "ok")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, inherited=None):
        super().__init__(addr, Handler, bind_and_activate=inherited is None)
        if inherited is not None:
            self.socket = inherited


def idle_watchdog(server):
    while True:
        time.sleep(30)
        if time.time() - _last_request > IDLE_EXIT:
            server.shutdown()
            return


def main():
    inherited = None
    if os.environ.get("LISTEN_FDS"):
        inherited = socket.socket(fileno=3)  # SD_LISTEN_FDS_START
    port = 9099
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    server = Server(("127.0.0.1", port), inherited)
    if inherited is not None:
        threading.Thread(target=idle_watchdog, args=(server,), daemon=True).start()
    else:
        print(f"svc-web on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

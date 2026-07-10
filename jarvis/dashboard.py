"""Local status dashboard for the Jarvis daemon (stdlib only, no deps).

Serves http://127.0.0.1:<port> — live view of Jarvis's state (idle /
listening / thinking / speaking), running background tasks across projects,
pending approvals, and the recent conversation. Read-only; bound to
localhost.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Jarvis</title><style>
body{font-family:-apple-system,Helvetica,sans-serif;background:#101418;color:#dde3ea;
     max-width:900px;margin:24px auto;padding:0 16px}
h1{font-size:20px} h2{font-size:14px;color:#8b98a5;text-transform:uppercase;
     letter-spacing:.08em;margin:24px 0 8px}
#state{display:inline-block;padding:4px 14px;border-radius:999px;font-weight:600;
     margin-left:10px;background:#333;transition:background .3s}
.idle{background:#3a4149}.listening{background:#1e7d43;animation:pulse 1.2s infinite}
.thinking{background:#a86412}.speaking{background:#1c64c8}.starting{background:#555}
@keyframes pulse{50%{opacity:.55}}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 8px;text-align:left;border-bottom:1px solid #232a31}
th{color:#8b98a5;font-weight:500}
.done{color:#4cc38a}.failed{color:#e5534b}.running{color:#e0a63f}.cancelled{color:#8b98a5}
#pending{background:#5c2e0e;border:1px solid #a86412;border-radius:8px;
     padding:10px 14px;margin:14px 0;display:none}
#log{font-size:13px;line-height:1.55}
#log .you{color:#7cb6ff}#log .jarvis{color:#dde3ea}#log .notice{color:#e0a63f}
small{color:#66727e}
</style></head><body>
<h1>Jarvis <span id="state" class="starting">…</span>
  <small style="float:right">brain: <span id="brain">?</span></small></h1>
<div id="pending"></div>
<h2>Tasks</h2>
<table><thead><tr><th>id</th><th>task</th><th>project</th><th>brain</th>
<th>status</th><th>latest</th></tr></thead><tbody id="tasks"></tbody></table>
<h2>Conversation</h2><div id="log"></div>
<script>
async function tick(){
  try{
    const s = await (await fetch('/state')).json();
    const st = document.getElementById('state');
    st.textContent = s.state; st.className = s.state;
    document.getElementById('brain').textContent = s.brain;
    const p = document.getElementById('pending');
    if(s.pending){p.style.display='block';
      p.textContent='⏳ Waiting for your approval (press hotkey, say yes/no): '+s.pending;}
    else p.style.display='none';
    document.getElementById('tasks').innerHTML = s.tasks.length ?
      s.tasks.map(t=>`<tr><td>${t.id}</td><td>${t.title}</td><td>${t.project}</td>
      <td>${t.brain}</td><td class="${t.status}">${t.status}</td>
      <td><small>${t.latest}</small></td></tr>`).join('')
      : '<tr><td colspan="6"><small>no tasks this session</small></td></tr>';
    document.getElementById('log').innerHTML =
      s.transcript.map(([w,t])=>`<div class="${w}"><b>${w}&gt;</b> ${t}</div>`).join('');
  }catch(e){document.getElementById('state').textContent='daemon offline';}
}
setInterval(tick, 1500); tick();
</script></body></html>"""


def start_dashboard(orch, io, port: int = 8787) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep the daemon log clean
            pass

        def _send(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/state":
                tasks = [
                    {
                        "id": t.id,
                        "title": t.title,
                        "project": t.project.name,
                        "brain": t.brain,
                        "status": t.status,
                        "latest": (t.result or (t.events[-1] if t.events else ""))[:160],
                    }
                    for t in orch.tasks.tasks.values()
                ]
                state = {
                    "state": getattr(io, "status", "unknown"),
                    "brain": orch.cfg.brain,
                    "pending": orch.pending.description if orch.pending else None,
                    "tasks": tasks,
                    "transcript": list(getattr(io, "transcript", [])),
                }
                self._send(json.dumps(state).encode(), "application/json")
            else:
                self._send(PAGE.encode(), "text/html; charset=utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

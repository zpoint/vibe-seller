"""Local pages that can only be read by a real browser.

Two things this has to get right, both learned the hard way.

**It must need a browser at all.** The first version served plain static
HTML with the answer in the markup, so the agent read it with ``curl`` —
entirely sensibly, since nothing about an unauthenticated static page
calls for a browser. Zero mux clients, and the slot assertion failed
for a reason that had nothing to do with slots. So the answer never
appears in the served markup: JS fetches it from ``/code/<n>`` at
runtime and injects it. ``curl`` on the page yields a placeholder.

**Interference must be arithmetic, not a judgement call.** Reading a
token proves a driver landed on the right page once; it says nothing
about two drivers staying apart across a long session, which is where
the real risk is. So each lab page keeps state only its OWN clicks may
change:

* ``#count``  — clicks on this page
* ``#trail``  — this page's id appended per click, e.g. ``2,2,2``
* ``#code``   — fetched and shown only once every section is visited

Two agents sharing a tab make that tab's counter absorb both click
streams while the other page's stays behind. The numbers stop matching.

Routes:
  ``/report/<n>``  one JS-rendered code, no interaction (Part B)
  ``/lab/<n>``     click through every section to reveal it (Part C)
  ``/code/<n>``    the code itself — fetched by JS, never in the markup
"""

from __future__ import annotations

import http.server
import json
import threading

# Sections a driver must visit before a lab page yields its code.
# Enough clicking to keep one busy while its siblings also work.
SECTIONS = ['overview', 'orders', 'returns', 'fees', 'summary']


def page_code(page_id: int) -> str:
    return f'LAB{page_id}-{"".join(s[0] for s in SECTIONS).upper()}'


_REPORT = """<!doctype html>
<title>Report {pid}</title>
<h1>Report {pid}</h1>
<p>Reference code: <b id="code">(rendering…)</b></p>
<script>
fetch('/code/{pid}')
  .then(r => r.text())
  .then(t => {{ document.getElementById('code').textContent = t; }});
</script>
"""

_LAB = """<!doctype html>
<title>Lab {pid}</title>
<h1>Dashboard {pid}</h1>
<p>Clicks on this page: <b id="count">0</b></p>
<p>Click trail: <span id="trail"></span></p>
<p>Sections visited: <span id="visited"></span></p>
<p>Reference code: <b id="code">(visit every section to reveal)</b></p>
<div id="buttons"></div>
<script>
const PID = {pid};
const SECTIONS = {sections};
const seen = new Set();
let n = 0;
const bdiv = document.getElementById('buttons');
SECTIONS.forEach(s => {{
  const b = document.createElement('button');
  b.id = 'btn-' + s;
  b.textContent = s;
  b.style = 'margin:6px;padding:14px 22px;font-size:16px';
  b.onclick = () => {{
    n += 1;
    seen.add(s);
    document.getElementById('count').textContent = n;
    const t = document.getElementById('trail');
    t.textContent = (t.textContent ? t.textContent + ',' : '') + PID;
    document.getElementById('visited').textContent =
      Array.from(seen).sort().join(',');
    if (seen.size === SECTIONS.length) {{
      fetch('/code/' + PID)
        .then(r => r.text())
        .then(x => {{ document.getElementById('code').textContent = x; }});
    }}
  }};
  bdiv.appendChild(b);
}});
</script>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: str, ctype: str = 'text/html; charset=utf-8'):
        raw = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parts = self.path.strip('/').split('/')
        if len(parts) == 2 and parts[1].isdigit():
            kind, pid = parts[0], int(parts[1])
            if kind == 'report':
                return self._send(_REPORT.format(pid=pid))
            if kind == 'lab':
                return self._send(
                    _LAB.format(pid=pid, sections=json.dumps(SECTIONS))
                )
            if kind == 'code':
                return self._send(page_code(pid), 'text/plain; charset=utf-8')
        self.send_error(404)

    def log_message(self, *a):
        pass


def start_click_lab() -> tuple[str, http.server.ThreadingHTTPServer]:
    """Start the lab on an ephemeral port; returns (base_url, server).

    Threading matters: several browsers hit this at once, and a
    serialising server would throttle the very concurrency under test.
    """
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{srv.server_address[1]}', srv

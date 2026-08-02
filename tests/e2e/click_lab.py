"""A click-heavy local site where tab interference is *arithmetic*.

Reading a token proves a driver landed on the right page once. It does
not prove the two drivers stayed apart across a long interactive
session — which is the real question for parallel subagents, because
the wedge risk is in sustained concurrent traffic, not the first load.

So each lab page keeps state that only its OWN clicks may change:

* ``#count``  — clicks on this page
* ``#trail``  — the page id appended per click, e.g. ``2,2,2``
* ``#code``   — revealed only after every section has been visited

If two agents end up driving one tab, that tab's counter absorbs both
click streams and its trail shows a single page id while the other
page's counter stays behind. Either way the numbers stop matching, and
the failure is a number, not a judgement call.

Served by :func:`start_click_lab`; used by the Part C live check and
available to any test that wants sustained concurrent browsing.
"""

from __future__ import annotations

import http.server
import threading

# Sections a driver must visit before the page yields its code. Enough
# clicking to keep a driver busy while its siblings are also working.
SECTIONS = ['overview', 'orders', 'returns', 'fees', 'summary']


def page_code(page_id: int) -> str:
    return f'LAB{page_id}-{"".join(s[0] for s in SECTIONS).upper()}'


_HTML = """<!doctype html>
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
const CODE = "{code}";
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
      document.getElementById('code').textContent = CODE;
    }}
  }};
  bdiv.appendChild(b);
}});
</script>
"""


def _render(pid: int) -> str:
    import json as _json

    return _HTML.format(
        pid=pid, sections=_json.dumps(SECTIONS), code=page_code(pid)
    )


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # /lab/<n>
        parts = self.path.strip('/').split('/')
        if len(parts) == 2 and parts[0] == 'lab' and parts[1].isdigit():
            body = _render(int(parts[1])).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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

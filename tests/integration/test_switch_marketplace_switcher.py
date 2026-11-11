"""Integration: bh_switch_marketplace.py against scrubbed switcher pages.

Serves each captured account-switcher layout from
tests/fixtures/account_switcher on a local HTTP server with a small
fake that owns the two behaviours the real page is (per live evidence
2026-11-11) inconsistent about:

  * which click TICKS a row -- a JS ``.click()`` (an UNTRUSTED event)
    on every store in the fleet today, or a real coordinate (trusted)
    click on the generation SKILL.md §4a-bis recorded;
  * which click CONFIRMS -- a click on the kat-button's shadow-root
    inner button (JS) vs a coordinate click on the host.

The fake gates on ``event.isTrusted`` exactly like those two
generations, and the fake ``ue_mid`` changes only when a confirm
arrives AFTER a row was ticked -- so a helper that clicks blind and
claims success fails here, honestly.

The REAL helper is piped to the REAL browser-use CLI (browser_harness)
attached to a headless Chromium, with ``SC_HOST=http://127.0.0.1:PORT``
(the origin form the helper documents for exactly this).

Negative control (run when touching the helper): ``git checkout
<parent> -- app/skills_v2/amazon-shared/scripts/bh_switch_marketplace.py``
and re-run this file -- the switch tests MUST fail there (the pre-fix
helper confirms with a coordinate click the js-layout fake ignores,
and cannot even address the origin form it never supported).
"""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import urllib.request

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / 'tests' / 'fixtures' / 'account_switcher'
HELPER = REPO / 'app/skills_v2/amazon-shared/scripts/bh_switch_marketplace.py'

SA = 'A17E79C6D8DWNP'
AE = 'A2VIGQ35RCS4UG'

# --- the switcher fake -------------------------------------------------
# MODE = {"row": "untrusted"|"trusted", "confirm": same}: which event
# trust level each control responds to. Rows map to marketplace ids
# through the page's own Intl.DisplayNames (real rows carry no ids).
FAKE_JS = """
<script>
(function(){
 var MODE = @MODE@;
 var MIDS = {US:'ATVPDKIKX0DER',CA:'A2EUQ1WTGCTBG2',MX:'A1AM78C64UM0Y8',
   BR:'A2Q3Y263D00KWC',AE:'A2VIGQ35RCS4UG',SA:'A17E79C6D8DWNP',
   AU:'A39IBJ37TRP1C6',EG:'ARBP9OOSHTCHU',SG:'A19VAU5U5O7RUS',
   JP:'A1VC38T7YXB528',IN:'A21TJRUUN4KGV',GB:'A1F83G8C2ARO7P'};
 var lang = document.documentElement.lang || 'en';
 var dn = null;
 try { dn = new Intl.DisplayNames([lang],{type:'region'}); } catch(e){}
 function nameOf(cc){ try { return dn ? dn.of(cc) : null; }
   catch(e){ return null; } }
 function match(t){
   for (var cc in MIDS){ var nm = nameOf(cc);
     if (nm && t.indexOf(nm) === 0) return {mid:MIDS[cc], nm:nm}; }
   return null;
 }
 document.querySelectorAll(
   'button.full-page-account-switcher-account-details')
 .forEach(function(b){
   if (b.querySelector(
     "kat-icon.full-page-account-switcher-account-expander-icon"))
     return;
   var lab = b.querySelector('.full-page-account-switcher-account-label');
   var t = ((lab||b).textContent||'').replace(/\\s+/g,' ').trim();
   var isCur = !!(lab && lab.getAttribute('data-test')
     === 'current-account');
   var m = match(t);
   if (!m) return;
   if (t.slice(m.nm.length).trim() && !isCur) return;  // pending
   b.__mid = m.mid;
   b.addEventListener('click', function(e){
     if (MODE.row === 'trusted' ? !e.isTrusted : e.isTrusted) return;
     document.querySelectorAll(
       '.full-page-account-switcher-account-selected')
     .forEach(function(x){
       x.classList.remove('full-page-account-switcher-account-selected');
     });
     b.classList.add('full-page-account-switcher-account-selected');
   });
 });
 if (!customElements.get('kat-button')) {
   customElements.define('kat-button', class extends HTMLElement {
     connectedCallback(){
       if (this.shadowRoot) return;
       var k = this;
       var sr = this.attachShadow({mode:'open'});
       sr.innerHTML = '<button class="button" type="button" '
         + 'style="width:100%;height:100%;border:0;background:'
         + 'transparent;color:inherit;font:inherit">'
         + '<slot><span>' + (k.getAttribute('label')||'')
         + '</span></slot></button>';
       sr.querySelector('button').addEventListener('click', function(e){
         if (k.getAttribute('data-test') !== 'confirm-selection')
           return;
         if (MODE.confirm === 'trusted' ? !e.isTrusted : e.isTrusted)
           return;
         var sel = null;
         document.querySelectorAll(
           'button.full-page-account-switcher-account-selected')
         .forEach(function(x){ if (x.__mid) sel = x; });
         if (!sel) return;   // confirm before a tick: nothing happens
         fetch('/__select',{method:'POST',body:sel.__mid})
           .then(function(){ location.href = '/home'; });
       });
     }
   });
 }
})();
</script>
"""

HOME_TMPL = (
    '<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
    '<title>Amazon</title></head><body><div id="sc">Seller Central'
    '</div><script>var ue_mid = "{mid}";</script></body></html>'
)


class _Server:
    """Stateful Seller Central fake: state = the session's ue_mid."""

    def __init__(self, fixture: str, mode: dict, initial_mid: str, lang: str):
        self.state = initial_mid
        self.fixture = fixture
        self.mode = mode
        self.lang = lang
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith('/home'):
                    body = HOME_TMPL.format(
                        lang=outer.lang, mid=outer.state
                    ).encode()
                elif self.path.startswith(
                    '/account-switcher/default/merchantMarketplace'
                ):
                    html = (
                        (FIXTURES / outer.fixture)
                        .read_text()
                        .replace(
                            '</body>',
                            FAKE_JS.replace('@MODE@', json.dumps(outer.mode))
                            + '</body>',
                        )
                    )
                    body = html.encode()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path == '/__select':
                    n = int(self.headers.get('Content-Length') or 0)
                    outer.state = self.rfile.read(n).decode().strip()
                    self.send_response(204)
                    self.end_headers()
                else:
                    self.send_error(404)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _browser_ws_url(port: int) -> str:
    with urllib.request.urlopen(
        f'http://127.0.0.1:{port}/json/version', timeout=10
    ) as r:
        return json.loads(r.read())['webSocketDebuggerUrl']


def _run_helper(browser_port: int, srv_port: int, cc: str, run_id: str) -> dict:
    """Pipe the real helper to the real browser-use CLI; return RESULT.

    The daemon runtime dir is a SHORT path (/tmp/...): macOS AF_UNIX
    sun_path caps at 104 bytes and pytest's tmp_path blows past it.
    """
    bu = Path(sys.executable).parent / 'browser-use'
    assert bu.exists(), f'browser-use CLI missing at {bu}'
    runtime = Path('/tmp') / f'vsw-{run_id}'
    shutil.rmtree(runtime, ignore_errors=True)
    (runtime / 'bh').mkdir(parents=True)
    (runtime / 'bhtmp').mkdir(parents=True)
    env = {
        'PATH': '/usr/bin:/bin:/usr/local/bin',
        'HOME': str(runtime),
        'BU_NAME': 'switchtest',
        'BU_CDP_WS': _browser_ws_url(browser_port),
        'BH_RUNTIME_DIR': str(runtime / 'bh'),
        'BH_TMP_DIR': str(runtime / 'bhtmp'),
        'SC_HOST': f'http://127.0.0.1:{srv_port}',
        'SC_MARKETPLACE': cc,
    }
    try:
        proc = subprocess.run(
            [str(bu)],
            input=HELPER.read_text(),
            env=env,
            capture_output=True,
            text=True,
            timeout=150,
        )
    finally:
        pidfile = runtime / 'bh' / 'bu.pid'
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text().strip()), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
        shutil.rmtree(runtime, ignore_errors=True)
    lines = [
        ln
        for ln in (proc.stdout or '').splitlines()
        if ln.startswith('RESULT ')
    ]
    assert lines, (
        f'no RESULT line; rc={proc.returncode} '
        f'stdout tail: {proc.stdout[-600:]!r} '
        f'stderr tail: {proc.stderr[-600:]!r}'
    )
    return json.loads(lines[-1][len('RESULT ') :])


async def _switch(fixture, mode, initial, cc, lang, run_id, browser_port):
    srv = _Server(fixture, mode, initial, lang)
    try:
        res = await asyncio.to_thread(
            _run_helper, browser_port, srv.port, cc, run_id
        )
        return res, srv.state
    finally:
        srv.stop()


@pytest.fixture(scope='module')
def _run_tag():
    return f'{os.getpid()}'


class TestSwitchMarketplace:
    """Each layout switches on the first call; missing rows fail honestly."""

    async def test_en_layout_js_clicks(self, _browser, _run_tag):
        """The layout every live store showed today: JS clicks tick the
        row and the shadow-root inner button confirms."""
        res, state = await _switch(
            'fullpage_en.html',
            {'row': 'untrusted', 'confirm': 'untrusted'},
            AE,
            'SA',
            'en-AE',
            _run_tag + 'en',
            _browser,
        )
        assert res['ok'] is True, res
        assert res['marketplace'] == SA
        assert res['row_method'] == 'js-click'
        assert res['confirm_method'] == 'shadow-js'
        assert state == SA

    async def test_zh_layout_js_clicks(self, _browser, _run_tag):
        """The zh-CN variant: labels and confirm come out of the page's
        own language -- the helper matches them with Intl, never words.
        """
        res, state = await _switch(
            'fullpage_zh.html',
            {'row': 'untrusted', 'confirm': 'untrusted'},
            SA,
            'AE',
            'zh-CN',
            _run_tag + 'zh',
            _browser,
        )
        assert res['ok'] is True, res
        assert res['marketplace'] == AE
        assert res['row_method'] == 'js-click'
        assert res['confirm_method'] == 'shadow-js'
        assert state == AE

    async def test_en_layout_coordinate_only(self, _browser, _run_tag):
        """The opposite generation (SKILL.md §4a-bis): only real
        coordinate clicks count. The helper must fall back and say so.
        """
        res, state = await _switch(
            'fullpage_en.html',
            {'row': 'trusted', 'confirm': 'trusted'},
            AE,
            'SA',
            'en-AE',
            _run_tag + 'co',
            _browser,
        )
        assert res['ok'] is True, res
        assert res['marketplace'] == SA
        assert res['row_method'] == 'coordinate-click'
        assert res['confirm_method'] == 'coordinate-click'
        assert state == SA

    async def test_missing_target_row_is_honest(self, _browser, _run_tag):
        """No row for the target: ok:false, reason says so, nothing
        was switched."""
        res, state = await _switch(
            'fullpage_en_no_target.html',
            {'row': 'untrusted', 'confirm': 'untrusted'},
            AE,
            'SA',
            'en-AE',
            _run_tag + 'no',
            _browser,
        )
        assert res['ok'] is False
        assert 'no selectable' in res.get('reason', ''), res
        assert state == AE  # unchanged

    async def test_already_on_target_short_circuits(self, _browser, _run_tag):
        """Target domain already renders the target: ok without
        touching the switcher."""
        res, state = await _switch(
            'fullpage_en.html',
            {'row': 'untrusted', 'confirm': 'untrusted'},
            AE,
            'AE',
            'en-AE',
            _run_tag + 'al',
            _browser,
        )
        assert res['ok'] is True, res
        assert res['row_method'] is None
        assert res['confirm_method'] is None
        assert state == AE

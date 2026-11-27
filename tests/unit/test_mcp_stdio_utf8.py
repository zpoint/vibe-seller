"""The in-task MCP server's stdio must be UTF-8 on every locale.

MCP's wire format is UTF-8 JSON, but Python's text stdin decodes with
the locale codec — GBK on a zh Windows box — so every non-ASCII tool
argument was mojibake'd before reaching the server (found live by the
C1 integration smoke: writing 设置 through
vibe_seller_write_workspace_file landed as mojibake while the tool
answered ok). main() must rebind both streams to UTF-8.
"""

import io

from app import mcp_server


def test_main_rebinds_stdio_to_utf8(monkeypatch):
    calls = {}

    class _FakeStream(io.TextIOWrapper):
        def reconfigure(self, **kw):
            calls[id(self)] = kw

    stdin = _FakeStream(io.BytesIO(b''), encoding='ascii')
    stdout = _FakeStream(io.BytesIO(), encoding='ascii')
    monkeypatch.setattr(mcp_server.sys, 'stdin', stdin)
    monkeypatch.setattr(mcp_server.sys, 'stdout', stdout)
    ran = {}

    def _fake_run(coro):
        ran['ok'] = True
        coro.close()

    monkeypatch.setattr(mcp_server.asyncio, 'run', _fake_run)
    monkeypatch.setattr(mcp_server, '_configure', lambda *a, **k: None)
    mcp_server.main()
    assert ran.get('ok') is True
    reconfigs = list(calls.values())
    assert {'encoding': 'utf-8', 'errors': 'replace'} in reconfigs
    assert {'encoding': 'utf-8'} in reconfigs

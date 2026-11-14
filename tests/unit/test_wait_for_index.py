"""scripts/wait_for_index.sh waits for the version's own wheel.

The v0.3.1 release audit started before 0.3.1 was installable: the wait
grepped the simple page for the bare version, and the page already said
"superseded by 0.3.1" in 0.3.0's yank reason.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'wait_for_index.sh'

YANKED_030 = (
    '<a href="https://files.example/vibe_seller-0.3.0-py3-none-any.whl"'
    ' data-yanked="Release incomplete; superseded by 0.3.1">'
    'vibe_seller-0.3.0-py3-none-any.whl</a>\n'
)
DEV_031 = (
    '<a href="https://files.example/vibe_seller-0.3.1.dev20261112-py3-none-any.whl">'
    'vibe_seller-0.3.1.dev20261112-py3-none-any.whl</a>\n'
)
REAL_031 = (
    '<a href="https://files.example/vibe_seller-0.3.1-py3-none-any.whl">'
    'vibe_seller-0.3.1-py3-none-any.whl</a>\n'
)


def _wait(tmp_path, page, version='0.3.1'):
    index = tmp_path / 'index.html'
    index.write_text(f'<html><body>\n{page}</body></html>\n')
    bash = shutil.which('bash')
    assert bash
    return subprocess.run(
        [bash, str(SCRIPT), index.as_uri(), version],
        env={'PATH': '/usr/bin:/bin', 'WAIT_TRIES': '1'},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_a_yank_reason_naming_the_version_is_not_the_release(tmp_path):
    r = _wait(tmp_path, YANKED_030)
    assert r.returncode == 1, r.stdout


def test_a_longer_dev_version_is_not_the_release(tmp_path):
    r = _wait(tmp_path, YANKED_030 + DEV_031)
    assert r.returncode == 1, r.stdout


def test_the_release_wheel_is_found(tmp_path):
    r = _wait(tmp_path, YANKED_030 + DEV_031 + REAL_031)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_dev_version_is_found_by_its_own_wheel(tmp_path):
    r = _wait(tmp_path, DEV_031, version='0.3.1.dev20261112')
    assert r.returncode == 0, r.stdout + r.stderr


def _index_by_accept(html: str, json_page: str):
    """A simple index that answers by Accept header, like PyPI's CDN:
    the PEP 691 JSON uv asks for and the HTML page are separate."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server's handler name
            accept = self.headers.get('Accept', '')
            body = json_page if 'pypi.simple.v1+json' in accept else html
            data = body.encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wait_http(server, version='0.3.1'):
    return subprocess.run(
        [
            shutil.which('bash'),
            str(SCRIPT),
            f'http://127.0.0.1:{server.server_address[1]}/simple/vibe-seller/',
            version,
        ],
        env={'PATH': '/usr/bin:/bin', 'WAIT_TRIES': '1', 'NO_PROXY': '*'},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_a_stale_json_page_is_not_the_release(tmp_path):
    """The nightly failure: the HTML page listed the wheel, the JSON page
    uv reads did not yet, and uv found no such version."""
    server = _index_by_accept(html=REAL_031, json_page='{"files": []}')
    try:
        r = _wait_http(server)
    finally:
        server.shutdown()
    assert r.returncode == 1, r.stdout + r.stderr


def test_both_representations_listing_it_is_the_release(tmp_path):
    json_page = (
        '{"files": [{"filename": "vibe_seller-0.3.1-py3-none-any.whl"}]}'
    )
    server = _index_by_accept(html=REAL_031, json_page=json_page)
    try:
        r = _wait_http(server)
    finally:
        server.shutdown()
    assert r.returncode == 0, r.stdout + r.stderr


def _fake_uv(tmp_path, script: str) -> Path:
    """A `uv` on PATH whose `pip compile` answers as *script* says.

    `uv` is the installer the audit runs next; the wait must not end
    before IT resolves the version, whatever the page said."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text('#!/bin/bash\n' + script)
    uv.chmod(0o755)
    return bin_dir


def _wait_with_uv(tmp_path, page, uv_script, tries, stable='2'):
    index = tmp_path / 'index.html'
    index.write_text(f'<html><body>\n{page}</body></html>\n')
    bin_dir = _fake_uv(tmp_path, uv_script)
    return subprocess.run(
        [shutil.which('bash'), str(SCRIPT), index.as_uri(), '0.3.1'],
        env={
            'PATH': f'{bin_dir}:/usr/bin:/bin',
            'WAIT_TRIES': tries,
            'WAIT_STABLE': stable,
            'FAKE_STATE': str(tmp_path / 'state'),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_listed_wheel_uv_cannot_resolve_is_not_the_release(tmp_path):
    """The v0.3.2 audit: the page listed the wheel, uv still found no
    such version — the edge uv reached was behind the one curl saw."""
    r = _wait_with_uv(tmp_path, REAL_031, 'exit 1', tries='3')
    assert r.returncode == 1, r.stdout + r.stderr


def test_one_lucky_resolution_is_not_enough(tmp_path):
    """uv answers yes, no, yes, no …: never twice in a row, so never
    stable — exactly what one edge caught up and the next not looks
    like."""
    flip = (
        'n=$(cat "$FAKE_STATE" 2>/dev/null || echo 0)\n'
        'echo $((n + 1)) > "$FAKE_STATE"\n'
        '[ $((n % 2)) -eq 0 ]\n'
    )
    r = _wait_with_uv(tmp_path, REAL_031, flip, tries='4')
    assert r.returncode == 1, r.stdout + r.stderr


def test_a_version_uv_resolves_steadily_is_the_release(tmp_path):
    late = (
        'n=$(cat "$FAKE_STATE" 2>/dev/null || echo 0)\n'
        'echo $((n + 1)) > "$FAKE_STATE"\n'
        '[ "$n" -ge 1 ]\n'
    )
    r = _wait_with_uv(tmp_path, REAL_031, late, tries='5')
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'resolved by uv 2 times in a row' in r.stdout


def test_every_index_wait_uses_the_script():
    wf = Path(__file__).resolve().parents[2] / '.github' / 'workflows'
    for name in ('release.yml', 'nightly.yml'):
        src = (wf / name).read_text()
        assert 'scripts/wait_for_index.sh' in src, name
        assert 'grep -qF "$VERSION"' not in src, name

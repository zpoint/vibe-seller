"""E2E: real agents install skills — a task installs a LOCAL skill
directory, the workspace assistant installs a skill FROM A URL.

Both flows are the owner story for the skill-import feature
(「创建任务让他直接拷贝本地的 xxx skill 到 vibe-seller 系统」 and the
Knowledge page's 「让助手从URL安装」), tested the way a user drives
them: the task description and the assistant message are PURE human
intent — no tool names, no mechanics — and the assertions are made
where the user would look: the Skills listing, and the installed tree
on the server's disk (this module OWNS its server, so its workspace
home is a known path: byte-for-byte checks and the executable bit are
read directly, not guessed over HTTP).

Runs in CI (no real creds; the "URL" is a loopback fixture — the
server is started with VIBE_ALLOW_LOOPBACK_WEBHOOKS=1, the same switch
the WeCom stub in test_skill_save_reuse.py needs).

Attribution: every slug here is minted from this module's own fixture
frontmatter (a unique name per run), so cleanup deletes exactly what
this test installed — the workspace namespace is shared with nothing
(owned server) and with no sibling (unique slug).
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import logging
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading
import time
import uuid

import pytest

from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    POLL_INTERVAL,
    create_store,
    create_task,
    poll_task_status,
)
from tests.e2e.own_server import owned_agent_stack

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

pytestmark = [pytest.mark.e2e]

# Pointed at by the module fixture (owned server).
BASE = ''
SKILLS_DIR = Path()

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)
BINARY = bytes(range(256)) * 16
RUN = b'#!/bin/sh\necho invoice-check\n'
NOTES = b'the method, in text\n'


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    """One server this module owns: its workspace home is on disk here,
    and loopback URL fetches are enabled for the assistant test."""
    global BASE, SKILLS_DIR
    with owned_agent_stack(
        tmp_path_factory,
        'skill-import',
        env={'VIBE_ALLOW_LOOPBACK_WEBHOOKS': '1'},
    ) as server:
        BASE = server.base_url
        SKILLS_DIR = server.home / '.claude' / 'skills'
        yield server


def _skill_md(name: str) -> str:
    return (
        f'---\nname: {name}\n'
        'description: "Check invoice PDFs against the ledger. Use when '
        'asked to verify an invoice."\n---\n'
        f'# {name}\n\nsteps here\n'
    )


def _build_source_dir(tag: str) -> tuple[Path, str]:
    """The fixture 'local skill' a user would point a task at."""
    name = f'invoice-check-{tag}'
    root = Path(tempfile.mkdtemp(prefix='vs-e2e-src-')) / name
    (root / 'assets').mkdir(parents=True)
    (root / 'scripts').mkdir()
    (root / 'SKILL.md').write_text(_skill_md(name), encoding='utf-8')
    (root / 'notes.txt').write_bytes(NOTES)
    (root / 'assets' / 'icon.png').write_bytes(PNG)
    run = root / 'scripts' / 'run.sh'
    run.write_bytes(RUN)
    run.chmod(0o755)
    return root, name


def _find_installed(md_text: str, timeout: float = 30) -> Path | None:
    """The installed skill dir, recognized by the SKILL.md this test
    authored (content-attributed — never a namespace guess)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if SKILLS_DIR.is_dir():
            for cand in SKILLS_DIR.iterdir():
                if not cand.is_dir() or cand.name.startswith('.'):
                    continue
                md = cand / 'SKILL.md'
                if md.is_file() and md.read_text(encoding='utf-8') == md_text:
                    return cand
        time.sleep(POLL_INTERVAL)
    return None


def _assert_tree(d: Path) -> None:
    assert (d / 'notes.txt').read_bytes() == NOTES
    assert (d / 'assets' / 'icon.png').read_bytes() == PNG
    run = d / 'scripts' / 'run.sh'
    assert run.read_bytes() == RUN
    assert run.stat().st_mode & 0o111, 'the executable bit was lost'
    # the installed set is EXACTLY the source tree (nested dirs and
    # all) — nothing dropped silently (the round-6 GitHub-walk bug
    # class, one install path so it applies to every source)
    names = sorted(str(p.relative_to(d)) for p in d.rglob('*') if p.is_file())
    assert names == [
        'SKILL.md',
        'assets/icon.png',
        'notes.txt',
        'scripts/run.sh',
    ], names


def _uninstall(md_text: str, api) -> None:
    """Delete exactly the skill dir this test authored (content-
    attributed — the fixture SKILL.md is ours, byte for byte)."""
    if not SKILLS_DIR.is_dir():
        return
    for cand in SKILLS_DIR.iterdir():
        md = cand / 'SKILL.md'
        if cand.is_dir() and md.is_file():
            if md.read_text(encoding='utf-8') == md_text:
                api.delete(f'{BASE}/api/workspace/skills/{cand.name}')


def _listed(api, slug: str) -> dict | None:
    resp = api.get(f'{BASE}/api/workspace/skills')
    resp.raise_for_status()
    return next((s for s in resp.json() if s.get('slug') == slug), None)


class TestTaskImportsLocalSkillDir:
    @pytest.mark.long_budget(330)
    def test_task_installs_directory(self, api_client):
        tag = f'{int(time.time())}-{uuid.uuid4().hex[:6]}'
        src, name = _build_source_dir(tag)
        md_text = _skill_md(name)
        try:
            store = create_store(api_client, f'e2e-skill-import-{tag}')
            # PURE human intent — no mechanics, no hints (a crutch
            # invalidates the test; if the agent goes astray the fix
            # is the product, not this string).
            task = create_task(
                api_client,
                title='Install the invoice-check skill',
                store_id=store['id'],
                description=f'Install the skill at {src} into Vibe Seller.',
            )
            final = poll_task_status(
                api_client,
                task['id'],
                {'completed'},
                fail_statuses={'failed'},
            )
            assert final['status'] == 'completed', (
                f'task failed: {final.get("error")}'
            )

            d = _find_installed(md_text)
            assert d is not None, (
                'no skill with this exact SKILL.md was installed'
            )
            _assert_tree(d)
            entry = _listed(api_client, d.name)
            assert entry is not None and entry['updatable'], entry
            assert entry['source'] == 'imported', (
                'a path import records provenance and must list as '
                f'imported, got: {entry}'
            )
        finally:
            _uninstall(md_text, api_client)
            shutil.rmtree(src.parent, ignore_errors=True)


# ── Assistant: install from a URL (loopback fixture) ──────────


@pytest.fixture
def url_source():
    """A loopback HTTP server serving ONE tar.gz (skill archive)."""
    state: dict = {'payload': b''}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            payload = state['payload']
            self.send_response(200)
            self.send_header('Content-Type', 'application/gzip')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {
            'state': state,
            'url': f'http://127.0.0.1:{port}/invoice-check.tar.gz',
        }
    finally:
        server.shutdown()
        server.server_close()


def _archive_for(name: str) -> bytes:
    md = _skill_md(name).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for rel, data, mode in (
            (f'{name}/SKILL.md', md, 0o644),
            (f'{name}/notes.txt', NOTES, 0o644),
            (f'{name}/assets/icon.png', PNG, 0o644),
            (f'{name}/scripts/run.sh', RUN, 0o755),
        ):
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestAssistantInstallsFromUrl:
    @pytest.mark.long_budget(360)
    def test_url_install_lists_as_imported(self, api_client, url_source):
        name = f'invoice-check-url-{uuid.uuid4().hex[:8]}'
        url_source['state']['payload'] = _archive_for(name)
        md_text = _skill_md(name)
        try:
            resp = api_client.post(
                f'{BASE}/api/workspace/assistant/message',
                json={
                    'content': f'从这个链接安装这个技能：{url_source["url"]}',
                    # what the UI sends: the user's picked profile
                    'profile_id': e2e_helpers.DEFAULT_PROFILE_ID,
                },
            )
            resp.raise_for_status()
            assert resp.json().get('ok'), resp.text

            deadline = time.time() + PIPELINE_TIMEOUT
            found = None
            while time.time() < deadline and found is None:
                found = _find_installed(md_text, timeout=POLL_INTERVAL)
                if found is None:
                    time.sleep(1)
            assert found is not None, (
                'the assistant did not install the skill from the URL'
            )
            _assert_tree(found)
            entry = _listed(api_client, found.name)
            assert entry is not None, entry
            assert entry['source'] == 'imported', (
                'URL installs record provenance — the 「导入」 badge '
                f'is exactly this entry: {entry}'
            )
        finally:
            api_client.post(f'{BASE}/api/workspace/assistant/stop')
            _uninstall(md_text, api_client)

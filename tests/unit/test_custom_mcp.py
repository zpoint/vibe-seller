"""Custom MCP servers: what reaches a launch is what Claude Code accepts.

Claude Code validates ``--mcp-config`` as a whole and EXITS on one
entry that fails its schema (``main.tsx``: ``process.exit(1)`` after
"Invalid MCP configuration"), so a vendor's ``{"url": …}`` with no
``type`` — the shape most vendors publish — would take every task
down. These pin the ONE gate (``normalize_server``), the shapes a user
pastes, the masks every response uses, and the launch wiring: the
per-launch file, the flags, and its deletion once the CLI has read it.
"""

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app import custom_mcp
from app.ai import claude_backend_utils as cbu
from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import mcp_launch_flags, task_mcp_flags
from app.ai.launch_artifacts import build_launch_artifacts
from app.custom_mcp import (
    McpConfigError,
    check_name,
    derive_name,
    display_label,
    launch_servers,
    normalize_server,
    parse_pasted,
    prompt_note,
    seal,
    unseal,
)
from app.custom_mcp_view import public_view
from app.url_redact import RedactUrlFilter, mask_url

pytestmark = pytest.mark.unit

TOKEN = 'tok-0123456789abcdef'
VENDOR_JSON = {
    'mcpServers': {
        'acme-data': {
            'url': 'https://mcp.example.com/mcp',
            'headers': {'Authorization': f'Bearer {TOKEN}'},
        }
    }
}


# ── normalize_server: the one gate ─────────────────────────────


def test_a_url_without_type_becomes_streamable_http():
    """THE pin: the vendor shape Claude Code would refuse is given the
    type it requires."""
    out = normalize_server('acme', {'url': 'https://h.example/mcp'})
    assert out == {'type': 'http', 'url': 'https://h.example/mcp'}


def test_a_url_ending_in_sse_is_the_legacy_sse_transport():
    out = normalize_server('acme', {'url': 'https://h.example/sse/'})
    assert out['type'] == 'sse'


@pytest.mark.parametrize(
    'declared', ['streamable-http', 'streamableHttp', 'HTTP', 'http']
)
def test_other_clients_spellings_of_http_are_accepted(declared):
    out = normalize_server('a', {'type': declared, 'url': 'https://h/mcp'})
    assert out['type'] == 'http'


def test_a_command_is_stdio_with_args_defaulted():
    out = normalize_server('a', {'command': 'npx', 'env': {'K': 'v'}})
    assert out == {
        'type': 'stdio',
        'command': 'npx',
        'args': [],
        'env': {'K': 'v'},
    }


def test_normalize_is_idempotent():
    """Re-run on every launch — a normalized config must pass as is."""
    once = normalize_server('a', VENDOR_JSON['mcpServers']['acme-data'])
    assert normalize_server('a', once) == once


def test_unknown_fields_are_dropped():
    out = normalize_server('a', {'url': 'https://h/mcp', 'timeout': 5})
    assert set(out) == {'type', 'url'}


@pytest.mark.parametrize(
    'raw, needle',
    [
        ({'url': 'https://h/mcp', 'headers': {'A': 'Bearer ${KEY}'}}, '${'),
        ({'command': 'x', 'env': {'K': '${ANTHROPIC_AUTH_TOKEN}'}}, '${'),
        ({'url': 'https://h/mcp', 'headers': {'A': '****abcd'}}, 'masked'),
        (
            {
                'url': 'https://h/mcp',
                'headers': {'A': 'Bearer 请改成您的 Token'},
            },
            'sample text',
        ),
        ({'url': 'https://h', 'oauth': {'clientId': 'x'}}, 'OAuth'),
        ({'url': 'https://h', 'headersHelper': 'sh x'}, 'headersHelper'),
        ({'url': 'ftp://h/mcp'}, 'http(s)'),
        ({'type': 'ws', 'url': 'wss://h'}, 'Unsupported'),
        ({'command': ''}, 'program'),
        ({'command': 'x', 'args': 'not-a-list'}, 'list'),
        ({'url': 'https://h', 'headers': {'Bad Name': 'v'}}, 'header'),
        ({'url': 'https://h', 'headers': {'A': 'x\r\nInjected: 1'}}, 'line'),
        ({'nothing': 'here'}, '"url"'),
        ('not an object', 'JSON object'),
    ],
)
def test_configs_claude_code_or_a_headless_task_cannot_use_are_refused(
    raw, needle
):
    with pytest.raises(McpConfigError, match=None) as exc:
        normalize_server('acme', raw)
    assert needle in str(exc.value)


@pytest.mark.parametrize(
    'name',
    ['vibe-seller', 'VIBE-SELLER', 'ticktick', 'playwright', 'browser-use'],
)
def test_names_the_platform_owns_are_refused(name):
    with pytest.raises(McpConfigError, match='uses itself'):
        check_name(name)


@pytest.mark.parametrize('name', ['a__b', '-x', 'has space', '中文', 'x' * 49])
def test_names_outside_the_cli_tool_name_shape_are_refused(name):
    with pytest.raises(McpConfigError, match='Invalid server name'):
        check_name(name)


# ── parse_pasted: what users paste ─────────────────────────────


def test_a_vendor_blurb_with_text_around_the_json_parses():
    blurb = '帮我安装这个 MCP，以下是配置 ' + json.dumps(VENDOR_JSON) + ' 谢谢'
    servers = parse_pasted(blurb)
    assert servers == {
        'example': {
            'type': 'http',
            'url': 'https://mcp.example.com/mcp',
            'headers': {'Authorization': f'Bearer {TOKEN}'},
        }
    }


def test_vs_code_servers_key_and_a_bare_map_parse():
    inner = VENDOR_JSON['mcpServers']
    assert parse_pasted({'servers': inner}) == parse_pasted(VENDOR_JSON)
    assert parse_pasted(inner) == parse_pasted(VENDOR_JSON)


def test_a_bare_url_is_named_after_its_domain_unless_named():
    url = 'https://mcp.example.com/mcp?mcp_token=abc123'
    assert parse_pasted(url) == {'example': {'type': 'http', 'url': url}}
    assert parse_pasted(url, name='acme') == {
        'acme': {'type': 'http', 'url': url}
    }


def test_an_unnamed_remote_server_takes_its_domain_not_its_json_key():
    """The panel promises "leave the name empty → named after the
    domain" — the vendor's JSON key (``acme-data``) does not win."""
    assert list(parse_pasted(VENDOR_JSON)) == ['example']
    paste = {
        'mcpServers': {
            'a': {'url': 'https://mcp.example.com/mcp'},
            'b': {'url': 'https://api.example.com/sse'},
            'files': {'command': 'npx', 'args': ['-y', '@acme/fs']},
        }
    }
    # Same domain twice gets -2; a local command has no domain and
    # keeps its key.
    assert list(parse_pasted(paste)) == ['example', 'example-2', 'files']


@pytest.mark.parametrize(
    'raw, name',
    [
        ({'url': 'https://mcp.example.com/mcp'}, 'example'),
        ({'url': 'https://api.example.com.cn/mcp'}, 'example'),
        ({'url': 'https://mcp.example.co.uk/sse'}, 'example'),
        ({'url': 'http://127.0.0.1:7792/mcp'}, '127-0-0-1'),
        ({'url': 'https://localhost/mcp'}, 'localhost'),
        # A name the platform owns is never derived as-is.
        ({'url': 'https://playwright.dev/mcp'}, 'playwright-mcp'),
        (
            {'command': 'npx', 'args': ['-y', '@acme/files-server', '/data']},
            'files-server',
        ),
        ({'command': 'npx', 'args': ['-y', '@acme/tools@1.2.3']}, 'tools'),
        ({'command': 'uvx', 'args': ['mcp-server-fetch']}, 'mcp-server-fetch'),
        ({'command': 'mytool'}, 'mytool'),
    ],
)
def test_derived_names_are_valid_and_readable(raw, name):
    assert derive_name(raw) == name
    assert check_name(name) == name


def test_the_typed_name_renames_a_single_server():
    assert list(parse_pasted(VENDOR_JSON, name='mine')) == ['mine']


def test_errors_carry_a_code_the_panel_can_translate():
    with pytest.raises(McpConfigError) as exc:
        parse_pasted(
            {'mcpServers': {'x': {'url': 'https://h', 'oauth': {'a': 1}}}},
            name='acme',
        )
    assert exc.value.code == 'oauth'
    assert exc.value.params == {'name': 'acme'}
    with pytest.raises(McpConfigError) as exc:
        check_name('ticktick')
    assert (exc.value.code, exc.value.params) == (
        'reserved_name',
        {'name': 'ticktick'},
    )


def test_one_bad_server_rejects_the_whole_paste_naming_it():
    paste = {
        'mcpServers': {
            'one': {'url': 'https://good.example/mcp'},
            'two': {'url': 'ftp://bad.example'},
        }
    }
    with pytest.raises(McpConfigError, match='^bad: '):
        parse_pasted(paste)


@pytest.mark.parametrize(
    'paste', ['', '{}', 'just words', '{"mcpServers": {}}']
)
def test_empty_or_unreadable_pastes_are_refused(paste):
    with pytest.raises(McpConfigError):
        parse_pasted(paste)


# ── storage and masking ────────────────────────────────────────


def test_sealed_config_is_not_readable_and_round_trips():
    config = parse_pasted(VENDOR_JSON)['example']
    stored = seal(config)
    assert TOKEN not in stored
    assert 'mcp.example.com' not in stored
    assert unseal(stored) == config


def test_an_unreadable_row_is_a_config_error():
    with pytest.raises(McpConfigError):
        unseal('not-a-fernet-token')


def test_public_view_masks_every_credential_shape():
    view = public_view({
        'type': 'stdio',
        'command': 'npx',
        'args': [
            '-y',
            'pkg',
            '--api-key',
            'sk-secret-value-1',
            '--token=tok-secret-2',
            'API_KEY=env-secret-3',
            'https://h.example/0123456789abcdefghij/mcp?key=q-secret-4',
            '--verbose',
        ],
        'env': {'SERVICE_TOKEN': 'env-secret-5', 'REGION': 'us'},
    })
    text = json.dumps(view)
    for secret in (
        'sk-secret-value-1',
        'tok-secret-2',
        'env-secret-3',
        '0123456789abcdefghij',
        'q-secret-4',
        'env-secret-5',
    ):
        assert secret not in text
    # What the list needs to say WHICH server this is stays readable.
    assert view['command'] == 'npx'
    assert view['args'][:3] == ['-y', 'pkg', '--api-key']
    assert view['args'][-1] == '--verbose'
    # Env values are masked whatever their names.
    assert view['env']['REGION'] != 'us'


def test_public_view_masks_header_values_and_url_query():
    view = public_view({
        'type': 'http',
        'url': 'https://u:pw@h.example/mcp?mcp_token=t0k3n',
        'headers': {'X-Anything': 'hdr-secret'},
    })
    assert view['url'] == 'https://****@h.example/mcp?mcp_token=****'
    assert view['headers']['X-Anything'].startswith('****')
    assert 'hdr-secret' not in json.dumps(view)


def test_mask_url_keeps_scheme_host_and_readable_path():
    assert mask_url('https://h.example:8443/v1/mcp') == (
        'https://h.example:8443/v1/mcp'
    )
    assert mask_url('not a url at all') == 'not a url at all'


def test_httpx_log_records_have_their_url_credentials_masked():
    """httpx logs every request URL at INFO; a URL can carry the key."""
    record = logging.LogRecord(
        'httpx',
        logging.INFO,
        __file__,
        1,
        'HTTP Request: %s %s "%s"',
        (
            'POST',
            'https://h.example/api/tasks/0f8fad5b-d9cb-469f-a165-70867728950e'
            '?mcp_token=t0k3n',
            'HTTP/1.1 200',
        ),
        None,
    )
    assert RedactUrlFilter().filter(record) is True
    assert 't0k3n' not in record.getMessage()
    # The path keeps its ids — they are what a log reader traces.
    assert (
        'https://h.example/api/tasks/0f8fad5b-d9cb-469f-a165-70867728950e'
        '?mcp_token=****'
    ) in record.getMessage()


# ── launch ─────────────────────────────────────────────────────


def test_launch_servers_skips_a_bad_row_and_keeps_the_rest(caplog):
    good = parse_pasted(VENDOR_JSON)['example']
    rows = [
        ('acme-data', seal(good)),
        ('broken', 'garbage'),
        ('vibe-seller', seal({'url': 'https://h/mcp'})),  # reserved since
    ]
    with caplog.at_level(logging.WARNING, logger='app.custom_mcp'):
        out = launch_servers(rows)
    assert out == {'acme-data': good}
    assert 'broken' in caplog.text and 'vibe-seller' in caplog.text


def test_a_failed_read_costs_the_servers_not_the_launch(monkeypatch):
    class _Broken:
        async def __aenter__(self):
            raise OperationalError('select', {}, Exception('no table'))

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(custom_mcp, 'async_session', lambda: _Broken())
    assert asyncio.run(custom_mcp.load_launch_servers()) == {}


def test_prompt_note_names_servers_and_their_tool_prefix():
    assert prompt_note({}) is None
    note = prompt_note({'zeta': None, 'acme': 'acme', 'xydc': '西柚MCP'})
    assert note.index('`acme`') < note.index('`xydc`') < note.index('`zeta`')
    assert 'mcp__acme__*' in note
    # What the user calls it, so 「用西柚MCP…」 finds the right tools.
    assert '`xydc` ("西柚MCP", tools `mcp__xydc__*`)' in note
    assert '"acme"' not in note  # a label equal to its key adds nothing


def test_a_name_in_another_script_is_a_label_and_the_domain_the_key():
    paste = {'mcpServers': {'x': {'url': 'https://mcp.xydc.com/mcp'}}}
    assert list(parse_pasted(paste, name='西柚MCP')) == ['xydc']
    assert display_label('西柚MCP') == '西柚MCP'
    # A name that can be a key as typed is the key.
    assert list(parse_pasted(paste, name='xiyou')) == ['xiyou']
    assert display_label('  ') is None
    with pytest.raises(McpConfigError):
        display_label('x' * 41)


def test_extra_configs_ride_along_strict_and_global():
    project = Path('/ws/.mcp.json')
    custom = Path('/ws/.system-prompt.custom-mcp.json')
    assert mcp_launch_flags(
        load_global=False,
        session_config=None,
        project_config=project,
        extra_configs=[custom],
    ) == ['--mcp-config', str(project), str(custom), '--strict-mcp-config']
    assert mcp_launch_flags(
        load_global=True,
        session_config=None,
        project_config=project,
        extra_configs=[custom],
    ) == ['--mcp-config', str(custom)]


def test_task_flags_without_custom_servers_are_unchanged(tmp_path):
    (tmp_path / '.mcp.json').write_text('{}')
    artifacts = {'user_mcp_config': None, 'custom_mcp_config': None}
    assert task_mcp_flags(tmp_path, False, artifacts) == [
        '--mcp-config',
        str(tmp_path / '.mcp.json'),
        '--strict-mcp-config',
    ]


def test_the_launch_file_is_owner_only_and_deleted_after_init(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cbu, 'VIBE_SELLER_DIR', tmp_path)
    servers = parse_pasted(VENDOR_JSON)
    artifacts = build_launch_artifacts(
        'task-1', tmp_path, 'prompt', None, custom_mcp=servers
    )
    path = artifacts['custom_mcp_config']
    assert path == cbu.custom_mcp_config_file('task-1', tmp_path)
    assert json.loads(path.read_text()) == {'mcpServers': servers}
    assert path.stat().st_mode & 0o077 == 0
    flags = task_mcp_flags(tmp_path, False, artifacts)
    assert str(path) in flags and flags[-1] == '--strict-mcp-config'

    cbu.discard_custom_mcp_config('task-1', tmp_path)
    assert not path.exists()
    cbu.discard_custom_mcp_config('task-1', tmp_path)  # idempotent


def test_no_custom_servers_writes_no_file(tmp_path):
    artifacts = build_launch_artifacts(
        'task-2', tmp_path, 'prompt', None, custom_mcp={}
    )
    assert artifacts['custom_mcp_config'] is None
    assert not cbu.custom_mcp_config_file('task-2', tmp_path).exists()


async def test_the_agents_init_event_deletes_the_launch_file(tmp_path):
    """The CLI reads --mcp-config files once, before it emits
    ``system/init`` (``main.tsx``); the stream handler deletes the
    third-party credentials as soon as that event arrives — a follow-up
    launch writes a fresh copy from the DB."""
    path = cbu.custom_mcp_config_file('init-task', tmp_path)
    path.write_text('{"mcpServers": {}}')
    session = AgentSession(
        task_id='init-task', prompt='p', mode='auto', task_dir=tmp_path
    )
    session.resume_session_id = 'already-persisted'  # no DB write
    session.session_id = 'already-seen'  # not the first init
    with patch.object(session, '_emit_message', AsyncMock()):
        await session._handle_event({
            'type': 'system',
            'subtype': 'init',
            'session_id': 'already-seen',
        })
    assert not path.exists()

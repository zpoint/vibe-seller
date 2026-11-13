"""Unit tests for the in-task ``vibe_seller_import_skill`` dispatch and
for the MCP process's restricted (``--tools``) session mode.

The dispatch branch is checked at THIS tier (which route, which body,
which bytes); the end-to-end install rides the workflow tier in
``tests/workflow/test_wf_skill_import.py``. The manager-side install
contract is ``tests/unit/test_mcp_skill_import.py``.
"""

import inspect
import io
import json
import tarfile
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_mcp.registry import tool_definitions
import app.mcp_server as mcp
from app.skill_uploads import entry_name

pytestmark = pytest.mark.unit

SKILL_MD = '---\nname: import-me\ndescription: d\n---\n# import-me\n'


def _skill_dir(tmp_path):
    d = tmp_path / 'the-skill'
    d.mkdir()
    (d / 'SKILL.md').write_text(SKILL_MD)
    (d / 'data.bin').write_bytes(b'\x00\x01\x02')
    return d


def _unpack(payload: bytes) -> dict:
    """tar.gz bytes -> {name: bytes} (what the route would receive)."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as tar:
        for member in tar:
            handle = tar.extractfile(member)
            out[entry_name(member.name)] = handle.read()
    return out


class TestImportDispatch:
    async def test_path_packs_and_posts_import_local(self, tmp_path):
        d = _skill_dir(tmp_path)
        with patch(
            'app.mcp_server.call_api',
            new_callable=AsyncMock,
            return_value={'slug': 'import-me', 'action': 'created'},
        ) as mock_api:
            result = await mcp.handle_tool_call(
                'vibe_seller_import_skill', {'path': str(d)}
            )
        assert json.loads(result)['slug'] == 'import-me'
        args, kwargs = mock_api.await_args
        method, path = args
        assert method == 'POST'
        assert path.startswith('/api/workspace/skills/import-local?')
        assert 'overwrite=false' in path
        assert 'origin_url=' in path  # provenance defaults to the path
        content = kwargs['content']
        assert isinstance(content, bytes)
        files = _unpack(content)
        assert files['SKILL.md'] == SKILL_MD.encode()
        assert files['data.bin'] == b'\x00\x01\x02'

    async def test_url_posts_import_url_json(self):
        with patch(
            'app.mcp_server.call_api',
            new_callable=AsyncMock,
            return_value={'slug': 'import-me'},
        ) as mock_api:
            await mcp.handle_tool_call(
                'vibe_seller_import_skill',
                {
                    'url': 'https://github.com/acme/skills/tree/main/x',
                    'overwrite': True,
                    'slug': 'chosen',
                },
            )
        args, kwargs = mock_api.await_args
        assert args == (
            'POST',
            '/api/workspace/skills/import-url',
            {
                'url': 'https://github.com/acme/skills/tree/main/x',
                'overwrite': True,
                'slug': 'chosen',
            },
        )

    @pytest.mark.parametrize(
        'arguments',
        [{}, {'path': '/x', 'url': 'https://e/x'}],
    )
    async def test_exactly_one_source(self, arguments):
        result = await mcp.handle_tool_call(
            'vibe_seller_import_skill', arguments
        )
        assert 'exactly one of path / url' in json.loads(result)['error']

    async def test_missing_skill_md_never_reaches_the_server(self, tmp_path):
        (tmp_path / 'bare').mkdir()
        with patch(
            'app.mcp_server.call_api', new_callable=AsyncMock
        ) as mock_api:
            result = await mcp.handle_tool_call(
                'vibe_seller_import_skill', {'path': str(tmp_path / 'bare')}
            )
        assert 'SKILL.md at its root' in json.loads(result)['error']
        mock_api.assert_not_awaited()

    async def test_pack_runs_off_the_event_loop(self):
        """Blocking walk in an async def is the gate's bug class; the
        branch must hand it to asyncio.to_thread."""
        src = inspect.getsource(mcp._import_skill)
        assert 'asyncio.to_thread(pack_skill_dir' in src


class TestRestrictedSession:
    def setup_method(self):
        mcp._ALLOWED_TOOLS = None
        mcp._config['task_id'] = None

    def teardown_method(self):
        mcp._ALLOWED_TOOLS = None
        mcp._config['task_id'] = None

    def test_unknown_tools_name_kills_the_session(self):
        with pytest.raises(SystemExit):
            mcp._configure(['--tools', 'vibe_seller_nope'])

    def test_restricted_session_needs_no_task_id(self):
        mcp._configure(['--tools', 'vibe_seller_list_skills'])
        assert mcp._ALLOWED_TOOLS == {'vibe_seller_list_skills'}

    def test_full_session_requires_task_id(self):
        with pytest.raises(SystemExit):
            mcp._configure([])

    def test_visible_tools_intersect(self):
        mcp._ALLOWED_TOOLS = {'vibe_seller_list_skills'}
        names = {t['name'] for t in mcp._visible_tools()}
        assert names == {'vibe_seller_list_skills'}

    async def test_dispatch_refuses_hidden_tools(self):
        mcp._ALLOWED_TOOLS = {'vibe_seller_list_skills'}
        result = await mcp.handle_tool_call(
            'vibe_seller_create_task', {'title': 'x'}
        )
        assert 'not available in this session' in json.loads(result)['error']


class TestListSkillsWithoutTask:
    async def test_assistant_mode_calls_the_plain_route(self):
        with (
            patch.dict(mcp._config, {'task_id': None}),
            patch(
                'app.mcp_server.call_api',
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_api,
        ):
            await mcp.handle_tool_call('vibe_seller_list_skills', {})
        mock_api.assert_awaited_once_with('GET', '/api/workspace/skills')

    async def test_task_mode_keeps_the_scoped_path(self):
        with (
            patch.dict(mcp._config, {'task_id': 't-1'}),
            patch(
                'app.mcp_server.call_api',
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_api,
        ):
            await mcp.handle_tool_call('vibe_seller_list_skills', {})
        mock_api.assert_awaited_once_with(
            'GET', '/api/workspace/skills?task_id=t-1'
        )


class TestUrlGuidanceIsVerbatim:
    """Observed failure (shots session): the assistant REWROTE a
    given skill URL into a raw api.github.com form before calling the
    tool, and the fetch then hit the real GitHub instead of the
    origin the user named. The tool text is the guard — pinned here
    for BOTH tool surfaces."""

    def _url_text(self, schema_node) -> str:
        return json.dumps(schema_node).lower()

    def test_in_task_tool_says_verbatim(self):
        tool = next(
            t for t in mcp.TOOLS if t['name'] == 'vibe_seller_import_skill'
        )
        blob = (
            tool['description'] + self._url_text(tool['inputSchema'])
        ).lower()
        assert 'verbatim' in blob
        assert 'never rewrite' in blob or 'do not rewrite' in blob

    def test_external_manage_skill_says_verbatim(self):
        defs = tool_definitions()
        entry = next(d for d in defs if d['name'] == 'manage_skill')
        blob = (
            entry['description'] + self._url_text(entry['inputSchema'])
        ).lower()
        assert 'verbatim' in blob

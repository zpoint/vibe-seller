"""A campaign-narrowed declaration binds at ACCESS time, not just in
the report.

CI 201i (test_ad_declaration_scoping, follow-up turn): the agent
declared ``audit`` with ``campaigns: [A1234567]`` and then ran an
"OTHER ROUTES PROBE" whose ``fetch()`` list included
``/campaign/A7654321`` — the excluded campaign. The report gate never
saw it; only the fake console's hit log caught it. The scope now
refuses the read on both product surfaces: the ads service call and
browser-use commands.

The deny is shape-based (CLAUDE.md) and target-position only: a
campaign id in tool output or a printed string is not a target.
"""

import pytest

from app.ai import ad_scope_access
from app.ai.ad_scope_access import ads_call_refusal, campaign_targets
from app.ai.tool_call_guards import ad_scope_access_deny, tool_call_deny

pytestmark = pytest.mark.unit

# The literal command from CI 201i (task e2867a43, 01:30:57) — the
# negative control: nothing refused it before this fix.
PROBE_COMMAND = """browser-use <<'PY'
import time
print(js("document.documentElement.outerHTML"))
print("=== OTHER ROUTES PROBE ===")
for p in ["/campaign/A1234567/keywords","/campaign/A1234567/search-terms","/keywords","/placements","/campaign/A7654321"]:
    r = js("fetch('%s').then(function(r){return r.status+' '+(r.headers.get('content-type')||'');})" % p)
    print(p, "->", r)
PY"""


def _declared(monkeypatch, kind='audit', campaigns=None, combos=None):
    scope = {}
    if combos is not None:
        scope['combos'] = combos
    if campaigns is not None:
        scope['campaigns'] = campaigns
    monkeypatch.setattr(
        'app.ai.ad_scope_access.load_declaration',
        lambda _tid: {'kind': kind, 'scope': scope},
    )


def test_the_ci_probe_command_is_denied_for_the_excluded_campaign(
    monkeypatch,
):
    _declared(monkeypatch, campaigns=['A1234567'])
    deny = ad_scope_access.refusal('task-e2867a43', PROBE_COMMAND)
    assert deny is not None
    assert 'A7654321' in deny
    assert 'A1234567' in deny  # it also says what IS allowed
    assert 'NEW user message' in deny  # and the way out


def test_the_same_probe_minus_the_excluded_campaign_is_allowed(
    monkeypatch,
):
    _declared(monkeypatch, campaigns=['A1234567'])
    command = PROBE_COMMAND.replace(',"/campaign/A7654321"', '')
    assert ad_scope_access.refusal('task', command) is None


def test_no_declaration_restricts_nothing(monkeypatch):
    monkeypatch.setattr(
        'app.ai.ad_scope_access.load_declaration', lambda _tid: None
    )
    assert ad_scope_access.refusal('task', PROBE_COMMAND) is None


def test_an_empty_campaigns_list_restricts_nothing(monkeypatch):
    _declared(monkeypatch, campaigns=[])
    assert ad_scope_access.refusal('task', PROBE_COMMAND) is None


def test_ids_in_output_or_printed_text_are_not_targets(monkeypatch):
    _declared(monkeypatch, campaigns=['A1234567'])
    # the id printed, compared, matched — but nothing ADDRESSES it
    command = (
        "browser-use <<'PY'\n"
        'print(js("document.body.innerText"))\n'
        'print("saw A7654321 on the page")\n'
        'print(js("document.title.includes(\'A7654321\')"))\n'
        'PY'
    )
    assert campaign_targets(command) == []
    assert ad_scope_access.refusal('task', command) is None


def test_every_real_console_shape_is_covered(monkeypatch):
    _declared(monkeypatch, campaigns=['A1234567'])
    for target in (
        '/campaign/A7654321',
        '/campaign/A7654321/keywords',
        '/campaign/details/A7654321',
        '/cm/sp/campaigns/A7654321?entityId=E123',
        '/cm/sb/campaigns/A7654321',
        'https://advertising.amazon.com/cm/sp/campaigns/A7654321',
        '/x?campaignId=A7654321&y=1',
    ):
        command = f"browser-use <<'PY'\njs(\"fetch('{target}')\")\nPY"
        deny = ad_scope_access.refusal('task', command)
        assert deny is not None, target


def test_the_declared_campaigns_are_readable(monkeypatch):
    _declared(monkeypatch, campaigns=['A1234567', 'A7654321'])
    assert ad_scope_access.refusal('task', PROBE_COMMAND) is None


def test_the_ads_call_surface_is_refused_too(monkeypatch):
    _declared(monkeypatch, campaigns=['A1234567'])
    deny = ads_call_refusal(
        'task', {'path': '/campaign/A7654321/keywords', 'params': {}}
    )
    assert deny is not None and 'A7654321' in deny
    assert (
        ads_call_refusal(
            'task', {'path': '/campaign/A1234567/keywords', 'params': {}}
        )
        is None
    )


def test_only_browser_use_commands_are_the_ad_surface(monkeypatch):
    _declared(monkeypatch, campaigns=['A1234567'])

    class _Session:
        task_id = 'task'

    assert ad_scope_access_deny(_Session(), 'Bash', {'command': PROBE_COMMAND})
    # an id in ordinary shell text is not a navigation target
    assert (
        ad_scope_access_deny(
            _Session(), 'Bash', {'command': 'echo /campaign/A7654321'}
        )
        is None
    )
    # and the wired chain reaches the same refusal
    assert tool_call_deny(
        _Session(),
        {'tool_name': 'Bash', 'tool_input': {'command': PROBE_COMMAND}},
    )

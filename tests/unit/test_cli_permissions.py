"""WeCom grants its bot each permission for 7 days, on its own clock.

``wecom-cli auth show`` keeps saying "authorized" after they expire, so
the only way to know is a real call: one READ-ONLY call per permission
(``PERMISSION_PROBES``), whose answer says whether that permission still
works. The responses below are the shapes the real CLI printed (bot ids
replaced): an expired permission answers errcode 850003 and names the
permission, with a renewal link for that one permission.
"""

import json

import pytest

from app import config
from app.cli_integrations import manager, paths, skills
from app.cli_integrations.base import CliIntegration
from app.cli_integrations.registry import INTEGRATIONS
from app.cli_integrations.wecom import (
    EXPIRED_ERRCODE,
    PERMISSION_PROBES,
    WeCom,
    parse_permission,
)

pytestmark = pytest.mark.unit

RENEW = (
    'https://work.weixin.qq.com/ai/aiHelper/authorizationList?from=chat'
    '&forceInnerBrowser=1&aibotid=100000000001&str_aibotid=aibEXAMPLE'
    '&type=7&ww_vw=640&ww_vh=640&for_native=true&bar_style_type=3'
    '&pc_bar_bg=f6f6f7%2C202021&hide_more_btn=true'
)


def _expired(name: str, url: str = RENEW) -> str:
    """What the CLI prints when *name*'s grant ran out."""
    return json.dumps(
        {
            'errcode': EXPIRED_ERRCODE,
            'errmsg': 'authorization expired, hint: [1], from ip: 0.0.0.0',
            'results_json': None,
            'help_instruction': '请务必将本次返回的 help_message 字段的值逐字原样展示给用户',
            'help_message': (
                f'当前机器人「{name}」使用权限已过期\\n若你是智能机器人创建者，'
                f'可以[点击这里]({url})授权当前机器人{name}使用权限；\\n'
                '若你不是机器人创建者，可联系该机器人创建者'
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


ALLOWED_EMPTY = json.dumps({
    'security_notice': 'x',
    'extra_identity_context': 'y',
    'schedule_list_count': 0,
})
ALLOWED_OK = json.dumps({'errcode': 0, 'errmsg': 'ok', 'docs': []})


def test_expired_permission_carries_its_own_renewal_link():
    p = parse_permission('mail', 1, _expired('邮件'), '')
    assert (p.key, p.state) == ('mail', 'expired')
    # The whole link, not cut at the markdown's ")" or the "\n" after it.
    assert p.renew_url == RENEW


def test_allowed_permission_with_or_without_an_errcode():
    assert parse_permission('calendar', 0, ALLOWED_EMPTY, '').state == 'ok'
    assert parse_permission('doc', 0, ALLOWED_OK, '').state == 'ok'


def test_a_check_that_failed_says_nothing_about_the_permission():
    """A timeout, another error or unreadable output is not "expired":
    nobody is sent to renew a permission that may be fine."""
    for rc, out, err in (
        (124, '', 'timeout'),
        (1, json.dumps({'errcode': 45009, 'errmsg': 'busy'}), ''),
        (0, 'not json', ''),
        (1, '', ''),
    ):
        p = parse_permission('doc', rc, out, err)
        assert (p.state, p.renew_url) == ('unknown', None), (rc, out, err)
    # Allowed is only an answer with no error: rc 0 with an errcode is not.
    assert parse_permission('doc', 0, json.dumps({'errcode': 1}), '').state == (
        'unknown'
    )


def test_expired_without_a_link_is_still_expired():
    body = json.dumps({'errcode': EXPIRED_ERRCODE, 'errmsg': 'expired'})
    p = parse_permission('contact', 1, body, '')
    assert (p.state, p.renew_url) == ('expired', None)


# Subcommands that change or send something — never a probe.
_WRITES = {
    'create',
    'send',
    'update',
    'delete',
    'cancel',
    'upload',
    'append',
    'overwrite',
    'finish',
    'rename',
    'import',
    'init',
}


def test_every_permission_is_checked_by_a_read_only_call():
    keys = [k for k, _ in PERMISSION_PROBES]
    assert len(keys) == len(set(keys))
    for key, args in PERMISSION_PROBES:
        assert not _WRITES & set(args), (key, args)
        assert args[-1] not in _WRITES


async def test_permissions_runs_every_probe_and_reads_each_answer():
    expired = {'contact': '通讯录', 'mail': '邮件', 'message': '消息'}
    seen: list[tuple[str, ...]] = []
    by_args = {tuple(a): k for k, a in PERMISSION_PROBES}

    async def run(args):
        seen.append(tuple(args))
        key = by_args[tuple(args)]
        if key in expired:
            return 1, _expired(expired[key]), ''
        return 0, ALLOWED_EMPTY, ''

    got = await WeCom().permissions(run)
    assert sorted(seen) == sorted(a for _, a in PERMISSION_PROBES)
    assert [p.key for p in got] == [k for k, _ in PERMISSION_PROBES]
    states = {p.key: p.state for p in got}
    assert {k for k, s in states.items() if s == 'expired'} == set(expired)
    assert all(p.renew_url == RENEW for p in got if p.state == 'expired')


def test_only_wecom_grants_permissions_one_by_one():
    flagged = {k for k, i in INTEGRATIONS.items() if i.per_permission}
    assert flagged == {'wecom'}
    assert CliIntegration.per_permission is False


def _umbrella(integ) -> str:
    return skills.umbrella_md(
        integ, '1.0.0', {'shared': {'SKILL.md': b'---\nname: x-shared\n---\n'}}
    )


def test_the_agent_sends_the_user_to_re_authorize_never_renews_itself():
    """Every platform's rules name the button the row shows once the
    sign-in or a permission expired; WeCom's also say how its CLI says a
    permission expired, so the agent recognises it."""
    for key, integ in INTEGRATIONS.items():
        text = _umbrella(integ)
        assert '重新授权' in text and 'Re-authorize' in text, key
        assert 'do not pass on a link from the error' in text, key
        # The page renders this target as a button to Settings →
        # Integrations (MessageBubble SETTINGS_LINKS).
        assert '](#integrations)' in text, key
    wecom = _umbrella(INTEGRATIONS['wecom'])
    assert '使用权限已过期' in wecom and str(EXPIRED_ERRCODE) in wecom
    assert '使用权限已过期' not in _umbrella(INTEGRATIONS['feishu'])


# ── a release's rules reach a platform enabled under older ones ──────


def _enabled_umbrella(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(config, 'VIBE_SELLER_DIR', home)
    integ = INTEGRATIONS['wecom']
    vdir = paths.version_dir('wecom', '1.3.4', home)
    vdir.mkdir(parents=True)
    exe = vdir / paths.exe_name(integ.binary)
    exe.write_text('#!/bin/sh\n')
    paths.write_manifest('wecom', {'version': '1.3.4'}, home)
    subs = {
        'shared': {'SKILL.md': b'---\nname: wecomcli-shared\n---\n# shared\n'},
        'email': {
            'SKILL.md': b'---\nname: wecomcli-email\ndescription: Mail.\n---\n',
            'references/a.md': b'ref\n',
        },
    }
    skills.install(integ, '1.3.4', subs, home)
    return home


async def test_boot_rewrites_an_umbrella_written_by_an_older_release(
    tmp_path, monkeypatch
):
    home = _enabled_umbrella(tmp_path, monkeypatch)
    umbrella = paths.skill_dir('wecom', home) / 'SKILL.md'
    current = umbrella.read_text(encoding='utf-8')
    ref = paths.skill_dir('wecom', home) / 'email' / 'references' / 'a.md'
    # As an older release wrote it: no re-authorize link.
    umbrella.write_text(
        current.replace('[Settings → Integrations](#integrations)', ''),
        encoding='utf-8',
    )
    ref_before = ref.read_bytes()

    assert await manager.refresh_umbrellas(home) == ['wecom']
    assert umbrella.read_text(encoding='utf-8') == current
    assert ref.read_bytes() == ref_before  # the vendor's files untouched
    # Already current: nothing rewritten.
    assert await manager.refresh_umbrellas(home) == []


async def test_boot_leaves_a_platform_that_is_not_enabled_alone(
    tmp_path, monkeypatch
):
    home = _enabled_umbrella(tmp_path, monkeypatch)
    skills.uninstall(INTEGRATIONS['wecom'], home)
    assert await manager.refresh_umbrellas(home) == []
    assert not (paths.skill_dir('wecom', home) / 'SKILL.md').exists()

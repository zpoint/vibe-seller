"""WeCom's authorization can expire while its CLI still says signed in.

The app only needs one answer: expired or not. It does not expose the
individual capability (mail, docs…) whose call discovered it.
"""

import json

import pytest

from app import config
from app.cli_integrations import manager, paths, skills
from app.cli_integrations.base import CliIntegration
from app.cli_integrations.registry import INTEGRATIONS
from app.cli_integrations.wecom import (
    EXPIRED_ERRCODE,
    EXPIRY_PROBES,
    WeCom,
    answered_expired,
)

pytestmark = pytest.mark.unit


def _expired() -> str:
    return json.dumps(
        {
            'errcode': EXPIRED_ERRCODE,
            'errmsg': 'authorization expired',
            'help_message': '当前机器人「邮件」使用权限已过期',
        },
        ensure_ascii=False,
    )


def test_expired_allowed_and_unknown_answers():
    assert answered_expired(1, _expired(), '') is True
    assert answered_expired(0, '{"schedule_list_count": 0}', '') is False
    assert answered_expired(0, '{"errcode": 0, "errmsg": "ok"}', '') is False
    for rc, out, err in (
        (124, '', 'timeout'),
        (1, '{"errcode": 45009}', ''),
        (0, 'not json', ''),
        (1, '', ''),
        (0, '{"errcode": 1}', ''),
    ):
        assert answered_expired(rc, out, err) is None, (rc, out, err)


def test_every_probe_is_read_only():
    writes = {
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
    assert len({tuple(args) for args in EXPIRY_PROBES}) == len(EXPIRY_PROBES)
    for args in EXPIRY_PROBES:
        assert not writes & set(args), args


async def test_one_expired_answer_means_the_authorization_expired():
    async def run(args):
        if args[:2] == ['mail', 'search']:
            return 1, _expired(), ''
        return 0, '{"items": []}', ''

    assert await WeCom().check_expired(run) is True

    async def allowed(args):
        return 0, '{"items": []}', ''

    assert await WeCom().check_expired(allowed) is False

    async def unknown(args):
        return 124, '', 'timeout'

    assert await WeCom().check_expired(unknown) is None


def test_only_wecom_needs_the_online_expiry_check():
    assert {k for k, i in INTEGRATIONS.items() if i.checks_expiry} == {'wecom'}
    assert CliIntegration.checks_expiry is False


def _umbrella(integ) -> str:
    return skills.umbrella_md(
        integ, '1.0.0', {'shared': {'SKILL.md': b'---\nname: x-shared\n---\n'}}
    )


def test_the_agent_sends_the_user_to_re_authorize_never_renews_itself():
    for key, integ in INTEGRATIONS.items():
        text = _umbrella(integ)
        assert '重新授权' in text and 'Re-authorize' in text, key
        assert 'do not pass on a link from the error' in text, key
        assert f'](#integrations-{key})' in text, key
    wecom = _umbrella(INTEGRATIONS['wecom'])
    assert '使用权限已过期' in wecom and str(EXPIRED_ERRCODE) in wecom
    assert '使用权限已过期' not in _umbrella(INTEGRATIONS['feishu'])


def _enabled_umbrella(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(config, 'VIBE_SELLER_DIR', home)
    integ = INTEGRATIONS['wecom']
    vdir = paths.version_dir('wecom', '1.3.4', home)
    vdir.mkdir(parents=True)
    (vdir / paths.exe_name(integ.binary)).write_text('#!/bin/sh\n')
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
    umbrella.write_text(
        current.replace('[Settings → Integrations](#integrations-wecom)', ''),
        encoding='utf-8',
    )
    ref_before = ref.read_bytes()

    assert await manager.refresh_umbrellas(home) == ['wecom']
    assert umbrella.read_text(encoding='utf-8') == current
    assert ref.read_bytes() == ref_before
    assert await manager.refresh_umbrellas(home) == []


async def test_boot_leaves_a_platform_that_is_not_enabled_alone(
    tmp_path, monkeypatch
):
    home = _enabled_umbrella(tmp_path, monkeypatch)
    skills.uninstall(INTEGRATIONS['wecom'], home)
    assert await manager.refresh_umbrellas(home) == []
    assert not (paths.skill_dir('wecom', home) / 'SKILL.md').exists()

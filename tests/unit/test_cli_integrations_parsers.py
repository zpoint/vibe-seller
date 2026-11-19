"""Each platform's login parser, fed the output its CLI really prints.

The lines are the shapes captured from real sign-ins (ids replaced):
the page must get the link / code / QR to act on, and — when a sign-in
ends with something the person must know — a notice with its detail and
a link, never a dump of the CLI's raw output.
"""

import json

import pytest

from app.cli_integrations.base import AuthStatus, LoginView
from app.cli_integrations.dingtalk import DingTalk
from app.cli_integrations.feishu import Feishu
from app.cli_integrations.wecom import WeCom

# Each platform's parser, taken the way the runner takes it: from the
# login steps for a signed-out CLI (Feishu's last step is the sign-in).
_OUT = AuthStatus(False, 'not_logged_in')
dingtalk = DingTalk().login_steps(_OUT)[0].parse
feishu_auth = Feishu().login_steps(_OUT)[-1].parse
wecom = WeCom().login_steps(_OUT)[0].parse

pytestmark = pytest.mark.unit


def _feed(parse, lines: list[str]) -> LoginView:
    view = LoginView()
    for line in lines:
        parse(line, view)
    return view


def _feishu_lines(complete: dict) -> list[str]:
    return [
        json.dumps(
            {
                'agent_hint': '[AI agent] 此命令最长阻塞约 10 分钟……' * 20,
                'event': 'device_authorization',
                'expires_in': 600,
                'user_code': 'ABCD-1234',
                'verification_uri': 'https://accounts.feishu.cn/oauth/v1/device/verify?flow_id=F1',
                'verification_uri_complete': 'https://accounts.feishu.cn/oauth/v1/device/verify?flow_id=F1&user_code=ABCD-1234',
            },
            ensure_ascii=False,
        ),
        '[lark-cli] device-flow: token response received',
        json.dumps(complete, ensure_ascii=False),
    ]


def test_feishu_signed_in_with_scopes_the_org_withheld():
    complete = {
        'event': 'authorization_complete',
        'granted': [f'im:scope{i}:read' for i in range(300)],
        'missing': ['vc:meeting.realtime:read'],
        'user_name': 'someone',
        'warning': {
            'hint': '[Restricted • Do not retry] Your organization admin has '
            'restricted these permissions: vc:meeting.realtime:read',
            'type': 'missing_scope',
        },
    }
    view = _feed(feishu_auth, _feishu_lines(complete))
    assert view.url.endswith('&user_code=ABCD-1234')
    assert view.user_code == 'ABCD-1234'
    assert view.notice == 'feishu_missing_scopes'
    assert view.notice_detail == 'vc:meeting.realtime:read'
    assert view.message is None  # no raw JSON as the page's message


def test_feishu_missing_scopes_read_from_the_hint_when_no_list():
    complete = {
        'event': 'authorization_complete',
        'warning': {
            'hint': 'admin has restricted these permissions: a:read b:write',
            'type': 'missing_scope',
        },
    }
    view = _feed(feishu_auth, _feishu_lines(complete))
    assert view.notice_detail == 'a:read b:write'


def test_feishu_clean_sign_in_has_no_notice():
    view = _feed(
        feishu_auth,
        _feishu_lines({'event': 'authorization_complete', 'granted': []}),
    )
    assert view.notice is None and view.message is None


DINGTALK_NO_ACCESS = [
    '● Step 1: 请求设备授权码...',
    '  授权码: WXYZ-1234',
    '  授权码将在 900 秒后过期。',
    '  授权链接（已填入授权码）：',
    'https://login.dingtalk.com/oauth2/device/verify.htm?caller=dws&user_code=WXYZ-1234',
    '  手动输入授权码的链接：',
    'https://login.dingtalk.com/oauth2/device/verify.htm?caller=dws',
    '● Step 2: 等待用户授权...',
    '授权成功!',
    '● Step 4: 检查组织 CLI 授权状态...',
    '',
    '⚠️  您暂无 CLI 数据访问权限',
    '   当前组织未授权您通过 CLI 访问个人数据。',
    '',
    '   组织主管理员：张三、李四',
    '   请联系组织主管理员开启后重新登录。',
    '',
    '   管理员操作入口：https://open-dev.dingtalk.com/fe/old#/developerSettings',
    '{',
    '  "error": {',
    '    "category": "auth",',
    '    "code": 2,',
    '    "message": "device authorization failed: 您暂无 CLI 数据访问权限，请联系管理员开启"',
    '  }',
    '}',
]


def test_dingtalk_org_without_cli_access_names_admins_and_console():
    view = _feed(dingtalk, DINGTALK_NO_ACCESS)
    assert view.user_code == 'WXYZ-1234'
    assert view.url.endswith('user_code=WXYZ-1234')  # the filled-in link
    assert view.notice == 'dingtalk_cli_access'
    assert view.notice_detail == '张三、李四'
    assert view.help_url == (
        'https://open-dev.dingtalk.com/fe/old#/developerSettings'
    )
    assert view.message == (
        'device authorization failed: 您暂无 CLI 数据访问权限，请联系管理员开启'
    )


def test_dingtalk_org_level_denial_still_points_at_the_console():
    view = _feed(dingtalk, ['', '⚠️  该组织尚未开启CLI数据访问权限', ''])
    assert view.notice == 'dingtalk_cli_access'
    assert view.help_url == 'https://open-dev.dingtalk.com'


def test_dingtalk_restarted_flow_replaces_code_and_link():
    view = _feed(dingtalk, DINGTALK_NO_ACCESS[:5])
    view = _feed(dingtalk, [*DINGTALK_NO_ACCESS[:5], '  授权码: NEW-0001'])
    assert view.user_code == 'NEW-0001' and view.url is None


def test_wecom_scan_link_then_qr_saved():
    view = _feed(
        wecom,
        [
            '正在获取二维码...',
            '请打开二维码链接扫码: ',
            'https://work.weixin.qq.com/ai/qc/gen?source=wecom_cli_external&scode=S1',
            '也可以使用企业微信扫描以下二维码：',
            '█▀▀▀▀▀█ ▄▀▄ █▀▀▀▀▀█',
        ],
    )
    assert view.url.startswith('https://work.weixin.qq.com/ai/qc/gen')
    assert view.qr is False  # the PNG is not written yet
    wecom('二维码已保存到: /tmp/session/qr.png', view)
    assert view.qr is True
    wecom(
        '{"error":{"type":"UnknownError","code":893999,"message":"QrTimeout: '
        '扫码超时（5 分钟），请重试 [code=893202]"}}',
        view,
    )
    assert view.message.startswith('QrTimeout')

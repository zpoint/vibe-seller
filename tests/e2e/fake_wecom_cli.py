#!/usr/bin/env python3
"""A stand-in for WeCom's official ``wecom-cli``, for e2e on a server
that cannot sign in to a real WeCom.

It answers the way the real CLI does for the parts the app relies on:
``--version``; ``auth show`` (signed in, a placeholder bot); and, for any
business command, either an answer or — when that command's permission
is listed as expired — errcode 850003 with the real response's shape: a
``help_message`` naming the permission and holding its own renewal link
(``type`` differs per permission). Which permissions are expired is read
on every call from ``$WECOM_CLI_CONFIG_DIR/standin.json``
(``{"expired": ["mail", ...]}``), so a test changes it while the server
runs. Every call's argv is appended to ``calls.log`` next to it.

``auth init`` (signing in) is refused: an agent must never run it.
"""

import json
import os
from pathlib import Path
import sys

STATE = Path(os.environ.get('WECOM_CLI_CONFIG_DIR') or '.')

# The permission a command needs, by its first word.
PERMISSION = {
    'doc': 'doc',
    'sheet': 'doc',
    'smartsheet': 'doc',
    'smartpage': 'doc',
    'slide': 'doc',
    'contact': 'contact',
    'message': 'message',
    'chat': 'message',
    'mail': 'mail',
    'disk': 'disk',
    'calendar': 'calendar',
    'meeting': 'meeting',
    'todo': 'todo',
}
NAME = {
    'doc': '文档',
    'contact': '通讯录',
    'message': '消息',
    'mail': '邮件',
    'disk': '微盘',
    'calendar': '日程',
    'meeting': '会议',
    'todo': '待办',
}
TYPE = {
    'doc': 1,
    'calendar': 2,
    'meeting': 3,
    'contact': 4,
    'todo': 5,
    'message': 6,
    'mail': 7,
    'disk': 8,
}
RENEW = (
    'https://work.weixin.qq.com/ai/aiHelper/authorizationList?from=chat'
    '&forceInnerBrowser=1&aibotid=100000000001&str_aibotid=aibEXAMPLE'
    '&type={type}&hide_more_btn=true'
)


def _expired() -> set[str]:
    try:
        data = json.loads((STATE / 'standin.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    return set(data.get('expired') or ())


def main(args: list[str]) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'calls.log').open('a', encoding='utf-8') as log:
        log.write(json.dumps(args) + '\n')
    if args[:1] in (['--version'], ['-V']):
        print('wecom-cli 1.3.4 (e2e stand-in)')
        return 0
    if args[:2] == ['auth', 'show']:
        print('Status: authorized\nBot ID: aibEXAMPLE')
        return 0
    if args[:1] == ['auth']:
        print(json.dumps({'error': {'message': 'e2e stand-in: no sign-in'}}))
        return 2
    if not args or '--help' in args or '-h' in args:
        print('wecom-cli (e2e stand-in): doc, contact, message, mail, ...')
        return 0
    key = PERMISSION.get(args[0])
    if key in _expired():
        name = NAME[key]
        url = RENEW.format(type=TYPE[key])
        print(
            json.dumps(
                {
                    'errcode': 850003,
                    'errmsg': 'authorization expired, hint: [0], from ip: '
                    '0.0.0.0, more info at '
                    'https://open.work.weixin.qq.com/devtool/query?e=850003',
                    'results_json': None,
                    'help_instruction': '请务必将本次返回的 help_message 字段'
                    '的值逐字原样（verbatim）展示给用户',
                    'help_message': f'当前机器人「{name}」使用权限已过期\\n'
                    f'若你是智能机器人创建者，可以[点击这里]({url})授权当前机器'
                    f'人{name}使用权限；\\n若你不是机器人创建者，可联系该机器人'
                    '创建者，前往企业微信「工作台-智能机器人」找到对应机器人进行授权',
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps({'errcode': 0, 'errmsg': 'ok', 'items': []}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

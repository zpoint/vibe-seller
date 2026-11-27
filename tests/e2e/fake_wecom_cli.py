#!/usr/bin/env python3
"""Stand-in wecom-cli for e2e: signed in, optionally expired.

``standin.json`` holds ``{"expired": true|false}``. When expired, a mail
call answers 850003 as WeCom does. Every call is logged; signing in is
refused. No real account or renewal link is involved.
"""

import json
import os
from pathlib import Path
import sys

STATE = Path(os.environ.get('WECOM_CLI_CONFIG_DIR') or '.')


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
    try:
        data = json.loads((STATE / 'standin.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if data.get('expired') and args[:1] == ['mail']:
        print(
            json.dumps(
                {
                    'errcode': 850003,
                    'errmsg': 'authorization expired',
                    'help_message': '当前机器人「邮件」使用权限已过期',
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps({'errcode': 0, 'errmsg': 'ok', 'items': []}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

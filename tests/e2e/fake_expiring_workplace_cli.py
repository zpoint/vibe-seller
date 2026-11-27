#!/usr/bin/env python3
"""Feishu / DingTalk stand-in whose official status command can expire."""

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve()
STATE = HERE.parents[2] / 'state' / 'standin.json'
BINARY = HERE.name


def expired() -> bool:
    try:
        return bool(
            json.loads(STATE.read_text(encoding='utf-8')).get('expired')
        )
    except (OSError, ValueError):
        return False


def main(args: list[str]) -> int:
    if args[:1] in (['--version'], ['-V']):
        print(f'{BINARY} 1.0.0 (e2e stand-in)')
        return 0
    gone = expired()
    if BINARY == 'lark-cli' and args[:2] == ['auth', 'status']:
        print(
            json.dumps({
                'appId': 'cli_example',
                'identities': {
                    'bot': {'status': 'ready'},
                    'user': {'status': 'verify_failed' if gone else 'ready'},
                },
            })
        )
        return 0
    if BINARY == 'dws' and args[:2] == ['auth', 'status']:
        if gone:
            print(
                json.dumps({
                    'authenticated': False,
                    'reason': 'token_refresh_failed',
                })
            )
        else:
            print(
                json.dumps({
                    'authenticated': True,
                    'user_name': 'Example User',
                    'corp_name': 'Example Corp',
                })
            )
        return 0
    print(json.dumps({'errcode': 0, 'items': []}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

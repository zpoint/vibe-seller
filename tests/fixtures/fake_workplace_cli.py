"""A stand-in workplace CLI for the ``app.cli_integrations`` tests.

Behaves like the real ones in the ways the framework depends on, with
its state in ``$FAKE_CLI_STATE`` (a directory):

    --version                     prints a version, exit 0
    auth status                   JSON {configured, logged_in, user}
    config init                   prints a setup URL, blocks until approved
    auth login [--qr <png>]       prints a device URL + code (or writes a
                                  QR PNG), blocks until approved; then
                                  exits $FAKE_CLI_LOGIN_EXIT (0), after a
                                  very long JSON line if
                                  $FAKE_CLI_LONG_LINE is set

Every run, like ``dws``, unpacks a runtime file (``RUNTIME``) next to
the executable — what Windows then holds open while it scans it.

"Approving" is a file the test drops: ``approve`` (consumed by each
step) or ``deny`` (the CLI exits 3, like a user who said no).
"""

import json
import os
from pathlib import Path
import sys
import time

PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8'
    b'\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)

RUNTIME = '.fake-runtime.lock'  # = fake_cli_integration.RUNTIME

state = Path(os.environ['FAKE_CLI_STATE'])
state.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
Path(sys.argv[0]).with_name(RUNTIME).touch()


def say(text: str) -> None:
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


def wait_for_approval() -> None:
    deadline = time.time() + float(os.environ.get('FAKE_CLI_WAIT_S', '30'))
    while time.time() < deadline:
        if (state / 'deny').exists():
            say('authorization denied by the user')
            sys.exit(3)
        approve = state / 'approve'
        if approve.exists():
            approve.unlink()
            return
        time.sleep(0.05)
    say('authorization expired')
    sys.exit(4)


if args == ['--version']:
    say(f'fakecli {os.environ.get("FAKE_CLI_VERSION", "1.2.3")}')
    sys.exit(0)

if args[:2] == ['auth', 'status']:
    token = state / 'token'
    say(
        json.dumps({
            'configured': (state / 'config').exists(),
            'logged_in': token.exists(),
            'user': token.read_text() if token.exists() else None,
        })
    )
    sys.exit(0)

if args[:2] == ['config', 'init']:
    say('Open https://fake.example/setup?app=APP1 to create the app')
    wait_for_approval()
    (state / 'config').write_text('app')
    say('configured')
    sys.exit(0)

if args[:2] == ['auth', 'login']:
    if '--qr' in args:
        Path(args[args.index('--qr') + 1]).write_bytes(PNG)
        say('scan the QR code with the app')
    else:
        say('Visit https://fake.example/device and enter code WXYZ-1234')
    (state / 'login-pid').write_text(str(os.getpid()))
    wait_for_approval()
    (state / 'token').write_text(os.environ.get('FAKE_CLI_USER', 'Alice'))
    # Like lark-cli when the org admin withheld scopes: signed in, one
    # long JSON line listing every scope, and a non-zero exit.
    if os.environ.get('FAKE_CLI_LONG_LINE'):
        granted = [f'scope{i}:read' for i in range(20000)]
        say(json.dumps({'event': 'authorization_complete', 'granted': granted}))
    say('logged in')
    sys.exit(int(os.environ.get('FAKE_CLI_LOGIN_EXIT', '0')))

sys.stderr.write(f'unknown command: {args}\n')
sys.exit(2)

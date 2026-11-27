"""The Windows body of the WeCom stand-in CLI.

On POSIX the stand-in is the shebang script itself (copied into the
version dir). Windows needs a real PE at ``wecom-cli.exe`` — the
integration runner spawns it with ``create_subprocess_exec`` and the
agent's shell resolves it by name — so this package exists to give
``pip`` a console script to build: ``pip install -e`` of this fixture
creates a genuine ``wecom-cli.exe`` shim in the venv's ``Scripts/``
that launches THIS venv's python.

The behaviour is not duplicated: the shim calls straight through to
:func:`tests.e2e.fake_wecom_cli.main`, the same code the POSIX stand-in
runs. It therefore only works inside an environment where the main
repo is importable — which is every environment that can build the
exe in the first place (the server's own clone venv).
"""

import sys


def console_main() -> int:
    from tests.e2e.fake_wecom_cli import main

    return main(sys.argv[1:])


if __name__ == '__main__':
    raise SystemExit(console_main())

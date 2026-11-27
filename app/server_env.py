"""The environment a vibe-seller server process starts with.

`vibe-seller start` launches the server either in this process
(foreground / --dev) or as a detached daemon. Both must start it with
the same few environment facts, so they live here instead of inline in
the CLI.
"""

import os
from pathlib import Path


def export_port(port: int) -> None:
    """Tell the server (and every process it starts) which port it is on.

    The server reads its own port from ``BACKEND_PORT`` (``app.config``)
    and hands it to everything that calls back into it: the in-task MCP
    (``--port``), the browser-use wrappers, the workspace assistant and
    the log file name. start.sh exports it; the CLI launcher did not,
    so a server started as ``vibe-seller start --port 7797`` told every
    agent to call 7777, and every in-task MCP tool failed with "All
    connection attempts failed". Set it before the foreground server
    imports ``app.config`` and before the daemon's env is copied.
    """
    os.environ['BACKEND_PORT'] = str(port)


def daemon_env() -> dict[str, str]:
    """The env for the detached server process (call after export_port)."""
    env = os.environ.copy()
    # Force UTF-8 text I/O for the daemon and every child it spawns
    # (agent, browser-use). Windows defaults to cp1252, which raises
    # UnicodeEncodeError on the non-ASCII (e.g. '→') in prompts and the
    # generated browser-use wrapper. This makes open()/write_text/
    # read_text default to UTF-8 process-wide, fixing the bug class
    # rather than one call site at a time.
    env['PYTHONUTF8'] = '1'
    # install.sh puts `npm install -g` payloads (claude CLI in
    # particular) into ~/.npm-global/bin/ and writes the matching
    # `export PATH=...` into the user's shell rc file. A user who runs
    # the documented two-liner (`curl … install.sh | bash` then
    # `vibe-seller start`) in the same shell has not sourced that rc
    # edit yet, so without this prepend the daemon starts with the old
    # PATH and the first task's `claude` spawn raises FileNotFoundError.
    npm_global_bin = Path.home() / '.npm-global' / 'bin'
    if npm_global_bin.is_dir():
        env['PATH'] = f'{npm_global_bin}{os.pathsep}{env.get("PATH", "")}'
    return env

"""The ONE thread hop for every launch artifact on disk.

``async def`` start() methods must not touch the disk on the event
loop (``scripts/check_blocking_async.py``). The system-prompt file,
the ``--settings`` payload and the browser-use guard are all ``mkdir`` + ``write_text`` work, so they
are built here — in ONE sync body the caller awaits through
``asyncio.to_thread`` — and the argv flags are extended from the
returned paths (pure). A leaf module: agent_proxy and
claude_backend_utils both reach the event-loop code, and this is the
one place that may import both.

"""

from app.ai.agent_proxy import launch_settings_file
from app.ai.claude_backend_utils import (
    apply_agent_venv_path,
    write_mcp_config_file,
    write_system_prompt_file,
)
from app.ai.claude_isolation import read_user_mcp_servers


def build_launch_artifacts(
    task_id: str,
    task_dir,
    system_prompt: str,
    settings: dict | None,
    *,
    env: dict | None = None,
    store_slug: str | None = None,
    mcp_config: dict | None = None,
    user_mcp_env: dict | None = None,
) -> dict:
    """Write EVERY launch artifact in ONE thread hop.

    ``async def`` start() methods must not touch the disk on the event
    loop: each of these writes is ``mkdir`` + ``write_text``, and the
    browser-use guard lay-down inside ``apply_agent_venv_path`` is the
    same. The caller awaits this whole body once through
    ``asyncio.to_thread`` and then extends its argv with the returned
    paths — argv extension is pure, the disk work is here.

    *settings* of ``None`` skips the ``--settings`` payload; *env* of
    ``None`` skips the venv/PATH wiring; *mcp_config* of ``None``
    skips the per-session ``--mcp-config`` payload (the workspace
    assistant's restricted skill-tool registration). *user_mcp_env*
    (the spawn env) reads the user-scope MCP servers out of the CLI's
    global config and writes them to a ``--mcp-config`` payload — for
    a profile that loads global MCP while the user setting source is
    off (``app.ai.claude_isolation``); ``None`` skips it, and so does
    a user config with no servers.
    """
    prompt_path = write_system_prompt_file(system_prompt, task_id, task_dir)
    settings_path = (
        launch_settings_file(task_id, task_dir, settings)
        if settings is not None
        else None
    )
    if env is not None:
        apply_agent_venv_path(env, store_slug)
    user_mcp_path = None
    if user_mcp_env is not None:
        servers = read_user_mcp_servers(user_mcp_env)
        if servers:
            user_mcp_path = write_mcp_config_file(
                {'mcpServers': servers},
                task_id,
                task_dir,
                suffix='.user-mcp.json',
            )
    return {
        'system_prompt': prompt_path,
        'settings': settings_path,
        'mcp_config': (
            write_mcp_config_file(mcp_config, task_id, task_dir)
            if mcp_config is not None
            else None
        ),
        'user_mcp_config': user_mcp_path,
    }

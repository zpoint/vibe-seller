"""The ONE thread hop for every launch artifact on disk.

``async def`` start() methods must not touch the disk on the event
loop (``scripts/check_blocking_async.py``). The system-prompt file,
the ``--settings`` payload, the ``--agents`` registration and the
browser-use guard are all ``mkdir`` + ``write_text`` work, so they
are built here — in ONE sync body the caller awaits through
``asyncio.to_thread`` — and the argv flags are extended from the
returned paths (pure). A leaf module: agent_proxy and
claude_backend_utils both reach the event-loop code, and this is the
one place that may import both.

"""

from app.ai.agent_proxy import launch_settings_file
from app.ai.claude_backend_utils import (
    apply_agent_venv_path,
    write_system_prompt_file,
)
from app.ai.reviewer_agent import write_agents_file


def build_launch_artifacts(
    task_id: str,
    task_dir,
    system_prompt: str,
    settings: dict | None,
    *,
    env: dict | None = None,
    store_slug: str | None = None,
    agents: bool = True,
) -> dict:
    """Write EVERY launch artifact in ONE thread hop.

    ``async def`` start() methods must not touch the disk on the event
    loop: each of these writes is ``mkdir`` + ``write_text``, and the
    browser-use guard lay-down inside ``apply_agent_venv_path`` is the
    same. The caller awaits this whole body once through
    ``asyncio.to_thread`` and then extends its argv with the returned
    paths — argv extension is pure, the disk work is here.

    *settings* of ``None`` skips the ``--settings`` payload; ``agents
    = False`` skips the ``--agents`` registration (the workspace
    assistant has no reviewer subagents); *env* of ``None`` skips the
    venv/PATH wiring.
    """
    prompt_path = write_system_prompt_file(system_prompt, task_id, task_dir)
    settings_path = (
        launch_settings_file(task_id, task_dir, settings)
        if settings is not None
        else None
    )
    agents_path = write_agents_file(task_id, task_dir) if agents else None
    if env is not None:
        apply_agent_venv_path(env, store_slug)
    return {
        'system_prompt': prompt_path,
        'settings': settings_path,
        'agents': agents_path,
    }

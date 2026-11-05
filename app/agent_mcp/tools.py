"""The operator-level toolset served by the hosted MCP endpoint.

Every tool is a thin adapter over the REST route handlers in
``app/routers/``. That is deliberate: an external agent must not be
able to reach a state the web UI cannot reach, and the only way to
guarantee that is to run the SAME code. The task state machine
(``app/task_states.py``), the plan lifecycle (``app/plan_states.py``),
the launch path (``app/task_launch.py``), the SSE events and the
permission checks all stay in one place — the route — and the MCP
tool calls it.

Two rules the adapters follow:

* **Send what the FRONTEND would send.** ``create_task`` leaves
  ``profile_id`` unset unless the caller names one, so the route
  resolves the user's ``default_profile_id`` exactly as the UI does.
  ``retry_task``, ``send_message`` and a ``review_plan`` reject send
  the CALLER'S DEFAULT when no profile is named — the same value the
  UI's composer posts, because its profile picker starts from
  ``default_profile_id`` and always sends it. So an omitted
  ``profile_id`` means "run it the way a person clicking the same
  button would", NOT "keep the profile of the run it replaces"
  (``"default"`` is a REAL profile id — plain Claude — not a "use the
  configured one" token).
* **Trim for context.** A task result can be tens of thousands of
  characters; an agent's context cannot. Long fields are cut with an
  explicit marker rather than silently.

Layout (the module was split when it passed the repo's 800-line
limit — this file is the assembly point that makes the imports run):

* ``registry.py`` — what a tool IS (``ToolDef``, the ``@tool``
  decorator, ``tools/list`` and lookup).
* ``support.py`` — the call context and the helpers every adapter
  reaches for.
* ``tools_tasks.py`` — list/get/wait, questions, plan review,
  messages, stop, retry.
* ``tools_task_create.py`` — ``create_task`` (with the UI's
  defer-create → upload-attachments → start chain).
* ``tools_task_files.py`` — ``get_task_log``, ``task_files`` and the
  image-confirm gate ``image_request``.
* ``tools_schedules.py`` — schedule CRUD + run, every result
  carrying the plan lifecycle (``lifecycle.plan_guidance``).
* ``tools_workspace.py`` — ``list_stores`` and the read-only
  ``read_knowledge`` (its path validation confines reads to
  ``knowledge/`` and ``stores/`` before anything is resolved).
* ``tools_workspace_files.py`` / ``tools_skills.py`` /
  ``tools_assistant.py`` — the workspace-file, skill-management and
  workspace-assistant tools (the operator parity expansion;
  ``files.py`` holds the shared file/upload contracts).
* ``tools_stores.py`` / ``tools_config.py`` / ``tools_comm.py`` /
  ``tools_ticktick.py`` —
  the operator-parity tools over the Settings pages (stores,
  browsers, Ziniao accounts, AI profiles, app settings + vision,
  email accounts, chat bots, TickTick). One tool per resource with
  an ``action`` enum; results pass through ``redact.redact`` so
  passwords/API keys/webhook secrets stay write-only.
"""

# Importing the tool modules is what registers their tools.
from app.agent_mcp import (
    tools_assistant,  # noqa: F401
    tools_comm,  # noqa: F401
    tools_config,  # noqa: F401
    tools_schedules,  # noqa: F401
    tools_skills,  # noqa: F401
    tools_stores,  # noqa: F401
    tools_task_create,  # noqa: F401
    tools_task_files,  # noqa: F401
    tools_tasks,  # noqa: F401
    tools_ticktick,  # noqa: F401
    tools_workspace,  # noqa: F401
    tools_workspace_files,  # noqa: F401
)
from app.agent_mcp.registry import (  # noqa: F401
    TOOLS,
    ToolDef,
    find_tool,
    tool_definitions,
)
from app.agent_mcp.support import McpContext, ToolError  # noqa: F401

__all__ = [
    'McpContext',
    'TOOLS',
    'ToolDef',
    'ToolError',
    'find_tool',
    'tool_definitions',
]

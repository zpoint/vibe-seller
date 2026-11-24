"""A parent's result once its sub-tasks are done: one section each.

The parent of a per-store split completes by itself when its last
sub-task finishes; this text is then its answer. Each sub-task gets its
own Markdown section, so two results never run together, and a long
one is cut at a word boundary with a pointer to the sub-task (which
holds the full text) — never mid-word, losing the answer.
"""

from typing import TYPE_CHECKING

from app.task_states import TaskStatus

if TYPE_CHECKING:
    from app.models.task import Task

# Per sub-task. The aggregate is the parent's chat answer; a full
# report stays one click away in its sub-task.
MAX_CHILD_CHARS = 1500


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_CHILD_CHARS:
        return text
    cut = text[:MAX_CHILD_CHARS]
    space = cut.rfind(' ')
    if space > MAX_CHILD_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip() + ' … (full result in the subtask)'


def aggregate_child_results(parent: 'Task', children: list['Task']) -> None:
    """Write one section per sub-task into ``parent.result``."""
    sections: list[str] = []
    for k in children:
        icon = '✓' if k.status == TaskStatus.COMPLETED else '✗'
        title = k.title or k.id[:8]
        detail = _clip(k.result or k.error or '')
        sections.append(f'### {icon} {title}\n\n{detail}'.rstrip())
    parent.result = '\n\n'.join(sections)

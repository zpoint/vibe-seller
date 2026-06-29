"""Hard structural backstop for ``review-collect`` at ``set_task_result``.

Where ``review_completeness_review`` is the soft converge-over-rounds
reviewer, this is the final binary check: if ANY enumerated product is
missing or its JSON malformed, deny the result so the ALC sync never
reads a half-finished run (the sync walks these files and upserts the
production DB — a partial run would silently drop ratings/reviews).

Soft fail-open after ``STALL_CAP`` denials so a parser/filesystem edge
case can never trap a task forever — the completeness reviewer is the
exhaustive coverage check; this is the cheap "don't ship a broken dump"
guard that makes a malformed per-product file impossible to publish
silently.
"""

from __future__ import annotations

from app.ai.review_manifest import audit_run
from app.ai.stop_gates import GateDeny, record_attempt

GATE_NAME = 'review_output_gate'

STALL_CAP = 3

_denials: dict[str, int] = {}


def reset_progress(task_id: str) -> None:
    """Drop per-task denial state (call on terminal success)."""
    _denials.pop(task_id, None)


def is_stalled(task_id: str) -> bool:
    """Fail open once the run has been denied ``STALL_CAP`` times."""
    return _denials.get(task_id, 0) >= STALL_CAP


def check(
    result_text: str,
    task_id: str | None = None,
    rules: dict | None = None,
) -> GateDeny | None:
    """Deny if any enumerated product file is missing or malformed."""
    if not task_id:
        return None
    audit = audit_run(task_id)
    if audit is None:
        return None  # not a resolvable store/review run → no-op

    problems: list[str] = []
    if not audit.manifest_present:
        problems.append(
            '无 _MANIFEST.json — 未采集任何商品；同步脚本无可读数据。'
        )
    else:
        problems.extend(f'采集不全 {s}' for s in audit.shortfalls)
        problems.extend(audit.defects)

    if not problems:
        return None

    record_attempt(task_id, GATE_NAME)
    _denials[task_id] = _denials.get(task_id, 0) + 1

    listed = '\n'.join(f'  - {p}' for p in problems[:25])
    more = '' if len(problems) <= 25 else f'\n  …及另外 {len(problems) - 25} 个'
    return GateDeny(
        gate=GATE_NAME,
        reason=(
            '采集输出不完整，无法交付：每个已枚举的商品都必须有一份合规的 '
            'per-product JSON（非空 rating、reviews 数组、collected_at），'
            'ALC 同步脚本会逐文件读取并写入生产库，半成品会导致评分/评论被悄悄'
            '丢弃。补齐下列问题后重新 set_task_result：\n'
            f'{listed}{more}'
        ),
    )

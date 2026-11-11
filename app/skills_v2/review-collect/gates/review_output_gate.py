"""Structural check for ``review-collect`` at ``set_task_result``.

If any enumerated product is missing or its JSON malformed, the first
submit is refused: the ALC sync walks these files and upserts the
production DB, so a half-finished dump would silently drop ratings and
reviews. One round, like every gate — what is still broken on the next
submit ships as caveats on the result.
"""

from __future__ import annotations

from app.ai.review_manifest import audit_run
from app.ai.stop_gates import GateDeny

GATE_NAME = 'review_output_gate'


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

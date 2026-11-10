"""Completeness check for ``review-collect`` runs.

Runs at ``set_task_result``. Walks the run's on-disk output (via
``app.ai.review_manifest``) and lists the combos still short of their
enumerated universe and the per-product JSON files that are missing or
malformed. Like every gate it gets one round: the first submit with
gaps is refused with this list, and what is still missing on the next
ships as caveats on the result.

Contract: ``app/skills_v2/review-collect/references/output-spec.md``.
"""

from __future__ import annotations

from app.ai.review_manifest import audit_run
from app.ai.stop_gates import GateDeny

GATE_NAME = 'review_completeness_review'


def check(
    result_text: str,
    task_id: str | None = None,
    rules: dict | None = None,
) -> GateDeny | None:
    """Return a gap diff, or None when every product is collected + clean."""
    if not task_id:
        return None
    audit = audit_run(task_id)
    if audit is None:
        return None  # not a resolvable store/review run → no-op

    gaps: list[str] = []
    if not audit.manifest_present:
        gaps.append(
            '没有找到 `reviews/_MANIFEST.json`（任务本地目录）。review-collect '
            '任务必须：先枚举每个 (platform, country) 的商品全集（amazon: All '
            'Listings Report 的 ASIN；noon: 商品目录），写入 _MANIFEST.json 的 '
            '`expected`，再逐商品下钻评论、写 per-product JSON、把 product_id '
            '加入该 combo 的 `collected`。'
        )
    else:
        gaps.extend(
            f'[未采全] 「{s}」——继续翻页采集剩余商品（缺失可接受，逐轮补全）'
            for s in audit.shortfalls
        )
        if audit.defects:
            sample = '；'.join(audit.defects[:8])
            more = (
                ''
                if len(audit.defects) <= 8
                else f' 等共 {len(audit.defects)} 个'
            )
            gaps.append(
                f'[残缺] {len(audit.defects)} 个已枚举商品的 JSON 缺失或不合规：'
                f'{sample}{more}。每个 per-product JSON 必须有非空 rating、'
                'reviews 数组、collected_at（按 output-spec 的 reviews/v1）。'
            )

    if not gaps:
        return None

    body = '\n'.join('- ' + g for g in gaps[:12])
    extra = '' if len(gaps) <= 12 else f'\n…还有 {len(gaps) - 12} 项'
    reason = (
        f'本轮采集仍有缺口（已合规 {audit.total_ok}/{audit.total_expected} '
        '个商品；缺失可接受——逐轮补全即可）。\n**这是续作（RESUME，不是重做）**：'
        '已写好的 per-product JSON 和 _MANIFEST 都还在，保留它们，只补下面列出的'
        '缺口——打开尚未采集的商品评论页（按日期排序、逐页翻到底），写对应 JSON，'
        '把 product_id 加入 manifest 的 collected。补完后重新 set_task_result；'
        '仍未补上的会作为备注随结果交付：\n' + body + extra
    )
    return GateDeny(gate=GATE_NAME, reason=reason, gaps=tuple(gaps))

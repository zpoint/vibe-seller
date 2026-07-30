/**
 * Hand a reviewed audit back to the agent as a follow-up.
 *
 * The console produces a decision submission; this posts it to the same
 * task via `/api/tasks/{id}/messages`, which RESUMES that task with its
 * full context rather than starting a new one. That matters: the agent
 * still has the drilled data and cached TSVs from the audit it just
 * finished, so execution does not re-derive what it already knows.
 *
 * The instruction text is deliberately NOT localised. It is an
 * agent-facing contract in the same vocabulary as the report and the
 * decision labels (see `vocabulary.ts`) — an executor must read the same
 * words whichever language the reviewer's UI happened to be in.
 */
import type { DecisionSubmission } from '../lib/adAudit/types'
import type { Task } from '../types'

export interface SubmitAuditApi {
  post(url: string, body: unknown): Promise<unknown>
  get(url: string): Promise<Task>
}

export interface SubmitAuditDeps {
  api: SubmitAuditApi
  profileId: string
  onOptimisticStatus?: (status: 'running') => void
  onError?: (err: unknown) => void
}

/**
 * The follow-up message body.
 *
 * Three things the agent has to be told, in this order:
 *  1. Act on THIS ONLY — the scope was narrowed on purpose.
 *  2. What was deliberately excluded, so "absent" is never read as
 *     "unknown" or "you forgot it".
 *  3. Record what it actually applied back into the per-campaign TSVs,
 *     which is what lets the NEXT audit see a change is fresh and hold
 *     instead of adjusting the same target again.
 */
export function auditFollowUpMessage(sub: DecisionSubmission): string {
  const ex = sub.excluded
  const excludedLines: string[] = []
  if (ex.countries.length) {
    excludedLines.push(`- 整个市场不执行：${ex.countries.join('、')}`)
  }
  if (ex.platforms.length) {
    excludedLines.push(`- 整个平台不执行：${ex.platforms.join('、')}`)
  }
  const single = ex.campaigns.filter((c) => c.excluded_at === 'campaign')
  if (single.length) {
    excludedLines.push(
      `- 单独排除的活动 ${single.length} 个：` +
        single.map((c) => c.campaign_name || c.campaign_id).join('、'),
    )
  }

  return [
    '广告审计已人工复核完毕。**只执行下面 JSON 里的决定**，其余一律不动。',
    '',
    `本次范围：${sub.totals.campaigns_in_scope} 个活动、` +
      `${sub.totals.rows_to_change} 行要改；` +
      `另有 ${sub.totals.campaigns_excluded} 个活动经人工确认本轮不执行。`,
    ...(excludedLines.length
      ? ['', '人工排除（不是遗漏，也不是没数据，是本轮特意不动）：', ...excludedLines]
      : []),
    '',
    '执行完成后，把**实际改了什么**写回对应的 per-campaign TSV：',
    '`applied_action` / `applied_at`(ISO 日期) / `previous_bid`。',
    '下一轮审计要靠这几列判断某一行刚动过、还在观察期，',
    '从而给出「维持（X 天前刚调过，数据不足）」而不是再调一次。',
    '没改成的行不要写这几列。',
    '',
    '```json',
    JSON.stringify(sub, null, 2),
    '```',
  ].join('\n')
}

export async function submitAuditDecisions(
  taskId: string,
  submission: DecisionSubmission,
  deps: SubmitAuditDeps,
): Promise<void> {
  // Mirrors continueTask: bump status optimistically BEFORE the await so
  // SSE updates arriving during the round-trip are not clobbered.
  deps.onOptimisticStatus?.('running')
  try {
    await deps.api.post(`/api/tasks/${taskId}/messages`, {
      content: auditFollowUpMessage(submission),
      profile_id: deps.profileId,
    })
  } catch (err) {
    deps.onError?.(err)
    throw err
  }
}

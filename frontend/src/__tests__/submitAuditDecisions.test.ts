// What leaves the page is what the agent will act on, so the follow-up
// has to be unambiguous about scope — and has to ask for the write-back
// that lets the NEXT audit know a change is fresh.

import { describe, expect, it, vi } from 'vitest'
import {
  auditFollowUpMessage,
  submitAuditDecisions,
} from '../handlers/submitAuditDecisions'
import type { DecisionSubmission } from '../lib/adAudit/types'

const base: DecisionSubmission = {
  markets: [
    {
      country: 'SA',
      currency: 'SAR',
      campaigns: [
        {
          campaign_id: '100000000001',
          campaign_name: 'acme widget auto',
          platform: 'amazon',
          currency: 'SAR',
          quarantined: false,
          rows: [
            {
              layer: 'kw',
              row_uid: 'kw#0',
              term: 'widget red',
              source_keyword: null,
              match: 'Exact',
              clicks: 40,
              spend: 80,
              current_bid: 2.0,
              action: 'raise',
              action_label: '提高出价',
              match_type: null,
              match_type_label: null,
              match_type_source: null,
              target_bid: 2.1,
              agent_suggested: 'raise',
              agent_suggested_label: '提高出价',
              overridden: false,
              locked_by_server: false,
              data_flag: null,
              agent_advice: '提高至 2.10',
            },
          ],
        },
      ],
    },
  ],
  excluded: { countries: [], platforms: [], campaigns: [] },
  totals: { campaigns_in_scope: 1, campaigns_excluded: 0, rows_to_change: 1 },
}

const withExclusions = (): DecisionSubmission => ({
  ...base,
  excluded: {
    countries: ['AE'],
    platforms: ['SA/noon'],
    campaigns: [
      {
        campaign_id: 'C_X',
        campaign_name: 'acme other',
        country: 'SA',
        platform: 'amazon',
        excluded_at: 'campaign',
      },
      {
        campaign_id: 'C_AE',
        campaign_name: null,
        country: 'AE',
        platform: 'amazon',
        excluded_at: 'country',
      },
    ],
  },
  totals: { campaigns_in_scope: 1, campaigns_excluded: 2, rows_to_change: 1 },
})

describe('auditFollowUpMessage', () => {
  it('says act on this only, and carries the payload', () => {
    const msg = auditFollowUpMessage(base)
    expect(msg).toContain('只执行下面 JSON 里的决定')
    expect(msg).toContain('```json')
    const json = JSON.parse(msg.split('```json\n')[1].split('\n```')[0])
    expect(json.markets[0].campaigns[0].campaign_id).toBe('100000000001')
  })

  it('states the scope in counts a reader can check', () => {
    expect(auditFollowUpMessage(base)).toContain('1 个活动、1 行要改')
  })

  it('names exclusions as deliberate, not missing', () => {
    // "Absent" alone would read as "the audit never covered it".
    const msg = auditFollowUpMessage(withExclusions())
    expect(msg).toContain('本轮特意不动')
    expect(msg).toContain('整个市场不执行：AE')
    expect(msg).toContain('整个平台不执行：SA/noon')
    // A campaign dropped on its own is listed separately from one that
    // went with its whole market. Checked against the PROSE only — the
    // JSON below it carries every exclusion, which is the point of it.
    const prose = msg.split('```json')[0]
    expect(prose).toContain('单独排除的活动 1 个')
    expect(prose).toContain('acme other')
    expect(prose).not.toContain('C_AE')
  })

  it('omits the exclusion section when nothing was excluded', () => {
    expect(auditFollowUpMessage(base)).not.toContain('人工排除')
  })

  it('asks for the write-back that powers the next audit cooldown', () => {
    const msg = auditFollowUpMessage(base)
    expect(msg).toContain('applied_action')
    expect(msg).toContain('applied_at')
    expect(msg).toContain('previous_bid')
    expect(msg).toContain('没改成的行不要写这几列')
  })
})

describe('submitAuditDecisions', () => {
  it('resumes the SAME task so the drilled data is reused', async () => {
    const post = vi.fn().mockResolvedValue({})
    await submitAuditDecisions('t1', base, {
      api: { post, get: vi.fn() },
      profileId: 'minimax',
    })
    expect(post).toHaveBeenCalledOnce()
    const [url, body] = post.mock.calls[0]
    expect(url).toBe('/api/tasks/t1/messages')
    expect((body as { profile_id: string }).profile_id).toBe('minimax')
  })

  it('bumps status before awaiting, so SSE updates are not clobbered', async () => {
    const order: string[] = []
    const post = vi.fn().mockImplementation(async () => {
      order.push('post')
    })
    await submitAuditDecisions('t1', base, {
      api: { post, get: vi.fn() },
      profileId: 'p',
      onOptimisticStatus: () => order.push('status'),
    })
    expect(order).toEqual(['status', 'post'])
  })

  it('reports a failed post instead of leaving the row stuck at running', async () => {
    const onError = vi.fn()
    const post = vi.fn().mockRejectedValue(new Error('boom'))
    await expect(
      submitAuditDecisions('t1', base, {
        api: { post, get: vi.fn() },
        profileId: 'p',
        onError,
      }),
    ).rejects.toThrow('boom')
    expect(onError).toHaveBeenCalledOnce()
  })
})

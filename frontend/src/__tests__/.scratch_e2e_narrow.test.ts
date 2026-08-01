// Scratch: run the console's real parse+narrow over the REAL report and
// REAL declaration produced by the live e2e run.
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { join } from 'path'
import { parseReport } from '../lib/adAudit/parseReport'
import { narrowToScope, opensConsole, droppedCampaigns, type AdDeclaration } from '../lib/adAudit/declaration'

const report = readFileSync(join(__dirname, '.e2e_result_fixture.md'), 'utf-8')

const decls: AdDeclaration[] = [
  { seq: 1, kind: 'investigate', scope: {}, user_turn: 1, created_at: '2026-08-01T15:19:40Z' },
  { seq: 2, kind: 'audit', scope: { campaigns: ['A1234567'] }, user_turn: 2, created_at: '2026-08-01T15:21:42Z' },
]

describe('live e2e report through the console pipeline', () => {
  it('the raw report really does contain the excluded campaign', () => {
    expect(report).toContain('A7654321')
  })

  it('phase 2 opens a console; phase 1 does not', () => {
    expect(opensConsole(decls[0])).toBe(false)
    expect(opensConsole(decls[1])).toBe(true)
  })

  it('the console shows ONLY the campaign the user asked about', () => {
    const full = parseReport(report)
    const narrowed = narrowToScope(full, decls[1])
    const ids = narrowed.sections.flatMap(s => s.campaigns.map(c => c.id))
    console.log('parsed campaigns:', full.sections.flatMap(s => s.campaigns.map(c => c.id)))
    console.log('after narrowing :', ids)
    console.log('hidden          :', droppedCampaigns(full, narrowed))
    expect(ids).not.toContain('A7654321')
    expect(ids.every(i => i === 'A1234567')).toBe(true)
  })
})

/**
 * F9: taskStates pure logic tests — getUI() for all statuses.
 */
import { describe, it, expect } from 'vitest'
import {
  getUI,
  hasProgressingTask,
  isProgressing,
  PROGRESSING_STATUSES,
  TASK_UI,
} from '../taskStates'

describe('getUI', () => {
  it('pending: inactive, no actions', () => {
    const ui = getUI('pending')
    expect(ui.isActive).toBe(false)
    expect(ui.canStopHeader).toBe(false)
    expect(ui.canSendMessage).toBe(false)
    expect(ui.canRetry).toBe(false)
    expect(ui.showModeToggle).toBe(true)
  })

  it('queued: inactive, no actions', () => {
    const ui = getUI('queued')
    expect(ui.isActive).toBe(false)
    expect(ui.canStopHeader).toBe(false)
    expect(ui.canSendMessage).toBe(false)
  })

  it('designing: active, stoppable, can send', () => {
    const ui = getUI('designing')
    expect(ui.isActive).toBe(true)
    expect(ui.canStopHeader).toBe(true)
    expect(ui.canSendMessage).toBe(true)
    expect(ui.isExecutePhase).toBe(false)
  })

  it('planned: stoppable, can send, shows plan confirm', () => {
    const ui = getUI('planned')
    expect(ui.isActive).toBe(false)
    expect(ui.canStopHeader).toBe(true)
    expect(ui.canSendMessage).toBe(true)
    expect(ui.showPlanConfirm).toBe(true)
    expect(ui.showModeToggle).toBe(true)
  })

  it('running: active, stoppable, can send, execute phase', () => {
    const ui = getUI('running')
    expect(ui.isActive).toBe(true)
    expect(ui.canStopHeader).toBe(true)
    expect(ui.canSendMessage).toBe(true)
    expect(ui.isExecutePhase).toBe(true)
    expect(ui.canRetry).toBe(false)
  })

  it('waiting: stoppable, can send, can wake, shows result', () => {
    const ui = getUI('waiting')
    expect(ui.canStopHeader).toBe(true)
    expect(ui.canSendMessage).toBe(true)
    expect(ui.canWake).toBe(true)
    expect(ui.showResult).toBe(true)
    expect(ui.isActive).toBe(false)
  })

  it('completed: can follow up, shows result, mode toggle visible', () => {
    const ui = getUI('completed')
    expect(ui.canStopHeader).toBe(false)
    expect(ui.canSendMessage).toBe(true)
    expect(ui.canRetry).toBe(true)
    expect(ui.showResult).toBe(true)
    expect(ui.isExecutePhase).toBe(true)
    expect(ui.showModeToggle).toBe(true)
  })

  it('failed: can retry and follow up, no stop, mode toggle visible', () => {
    const ui = getUI('failed')
    expect(ui.canRetry).toBe(true)
    expect(ui.canSendMessage).toBe(true)
    expect(ui.canStopHeader).toBe(false)
    expect(ui.isExecutePhase).toBe(true)
    expect(ui.showModeToggle).toBe(true)
  })

  it('unknown status falls back to pending config', () => {
    const ui = getUI('nonexistent_status')
    expect(ui).toEqual(TASK_UI.pending)
  })
})


/**
 * PROGRESSING vs terminal semantics. This is the authoritative
 * frontend statement of which statuses gate the schedule "Run Now"
 * trigger button. Must stay in sync with the backend table in
 * `app/task_states.py` (`TRANSITIONS`) and the group assertions in
 * `tests/unit/test_task_states.py::TestStateGroupSemantics`.
 *
 * If any row below changes, update the backend mirror in the same
 * commit — a divergence between the two lets the UI disable the
 * button while the backend would accept a Trigger, or vice versa.
 */
describe('progressing vs terminal classification', () => {
  const CASES: Array<[status: string, progressing: boolean, reason: string]> = [
    ['pending', true, 'queued for pipeline, not started'],
    ['queued', true, 'waiting on concurrency slot'],
    ['designing', true, 'agent is planning'],
    ['planned', true, 'plan awaiting approval / execute-plan'],
    ['running', true, 'agent is executing'],
    ['waiting', false, 'agent set a wait-condition; a fresh Trigger is fine'],
    ['completed', false, 'happy-path terminal'],
    ['failed', false, 'error terminal'],
  ]

  it.each(CASES)(
    '%s → progressing=%s (%s)',
    (status, expected) => {
      expect(isProgressing(status)).toBe(expected)
    },
  )

  it('PROGRESSING_STATUSES exactly matches the expected progressing set', () => {
    const expected = new Set(CASES.filter(([, p]) => p).map(([s]) => s))
    expect(new Set(PROGRESSING_STATUSES)).toEqual(expected)
  })

  it('unknown status is treated as NOT progressing (fail-safe)', () => {
    // A garbled status shouldn't lock the Run Now button —
    // backend will reject the trigger if it's truly active.
    expect(isProgressing('some_unknown_value')).toBe(false)
  })

  it('hasProgressingTask returns true when any task is progressing', () => {
    expect(
      hasProgressingTask([{ status: 'completed' }, { status: 'running' }]),
    ).toBe(true)
  })

  it('hasProgressingTask returns false for all-terminal lists', () => {
    expect(
      hasProgressingTask([
        { status: 'completed' },
        { status: 'failed' },
        { status: 'waiting' },
      ]),
    ).toBe(false)
  })

  it('hasProgressingTask returns false for empty list', () => {
    expect(hasProgressingTask([])).toBe(false)
  })

  it('regression: waiting alone does NOT disable trigger', () => {
    // This is the bug the test exists to prevent. A schedule
    // whose only prior task is in `waiting` must still allow
    // Run Now — otherwise long-running watcher tasks
    // permanently lock the button.
    expect(hasProgressingTask([{ status: 'waiting' }])).toBe(false)
  })
})


/**
 * Full button / UI-flag matrix per task status.
 *
 * Each row is the complete TASK_UI config expected for that
 * status. The test compares `getUI(status)` to this row. This is
 * the authoritative statement of "which button is visible /
 * enabled in which state" and is the pair to the backend
 * `TRANSITIONS` table — mutation-testable by flipping any single
 * flag.
 *
 * Columns (TaskUIConfig keys, in stable order):
 *   isExecutePhase, isActive, canStopHeader, canSendMessage,
 *   canRetry, showResult, showModeToggle, showPlanConfirm, canWake
 */
describe('TASK_UI button matrix — exhaustive per status', () => {
  type Flag =
    | 'isExecutePhase'
    | 'isActive'
    | 'canStopHeader'
    | 'canSendMessage'
    | 'canRetry'
    | 'showResult'
    | 'showModeToggle'
    | 'showPlanConfirm'
    | 'canWake'

  const FLAGS: Flag[] = [
    'isExecutePhase',
    'isActive',
    'canStopHeader',
    'canSendMessage',
    'canRetry',
    'showResult',
    'showModeToggle',
    'showPlanConfirm',
    'canWake',
  ]

  // Each row = 9 booleans in FLAGS order.
  const MATRIX: Record<string, Record<Flag, boolean>> = {
    pending: {
      isExecutePhase: false,
      isActive: false,
      canStopHeader: false,
      canSendMessage: false,
      canRetry: false,
      showResult: false,
      showModeToggle: true,
      showPlanConfirm: false,
      canWake: false,
    },
    queued: {
      isExecutePhase: false,
      isActive: false,
      canStopHeader: false,
      canSendMessage: false,
      canRetry: false,
      showResult: false,
      showModeToggle: true,
      showPlanConfirm: false,
      canWake: false,
    },
    designing: {
      isExecutePhase: false,
      isActive: true,
      canStopHeader: true,
      canSendMessage: true,
      canRetry: false,
      showResult: false,
      showModeToggle: true,
      showPlanConfirm: false,
      canWake: false,
    },
    planned: {
      isExecutePhase: false,
      isActive: false,
      canStopHeader: true,
      canSendMessage: true,
      canRetry: false,
      showResult: false,
      showModeToggle: true,
      showPlanConfirm: true,
      canWake: false,
    },
    running: {
      isExecutePhase: true,
      isActive: true,
      canStopHeader: true,
      canSendMessage: true,
      canRetry: false,
      showResult: false,
      showModeToggle: true,
      showPlanConfirm: false,
      canWake: false,
    },
    waiting: {
      isExecutePhase: true,
      isActive: false,
      canStopHeader: true,
      canSendMessage: true,
      canRetry: false,
      showResult: true,
      showModeToggle: true,
      showPlanConfirm: false,
      canWake: true,
    },
    completed: {
      isExecutePhase: true,
      isActive: false,
      canStopHeader: false,
      canSendMessage: true,
      canRetry: true,
      showResult: true,
      showModeToggle: true,
      showPlanConfirm: false,
      canWake: false,
    },
    failed: {
      isExecutePhase: true,
      isActive: false,
      canStopHeader: false,
      canSendMessage: true,
      canRetry: true,
      showResult: false,
      showModeToggle: true,
      showPlanConfirm: false,
      canWake: false,
    },
  }

  // Row-level assertion: every flag for every status.
  for (const [status, expected] of Object.entries(MATRIX)) {
    it(`${status} config matches expected row`, () => {
      expect(getUI(status)).toEqual(expected)
    })
  }

  // Cell-level parametrized assertion: mutation-safe coverage.
  // If any single flag flips in TASK_UI without updating MATRIX,
  // the specific cell-level test names the offender.
  const CELLS: Array<[string, Flag, boolean]> = []
  for (const [status, row] of Object.entries(MATRIX)) {
    for (const flag of FLAGS) {
      CELLS.push([status, flag, row[flag]])
    }
  }
  it.each(CELLS)('%s.%s === %s', (status, flag, expected) => {
    expect(getUI(status)[flag]).toBe(expected)
  })

  // Invariants across the matrix.
  it('exactly one status allows canWake (waiting)', () => {
    const wakeable = Object.entries(MATRIX).filter(
      ([, row]) => row.canWake,
    )
    expect(wakeable.map(([s]) => s)).toEqual(['waiting'])
  })

  it('canStopHeader ⊆ { active | waiting | planned }', () => {
    // Only active / planned / waiting tasks should expose a
    // header Stop button — everything else is terminal or
    // pre-run.
    for (const [status, row] of Object.entries(MATRIX)) {
      if (row.canStopHeader) {
        expect(['designing', 'planned', 'running', 'waiting']).toContain(
          status,
        )
      }
    }
  })

  it('canRetry only on terminal statuses', () => {
    for (const [status, row] of Object.entries(MATRIX)) {
      if (row.canRetry) {
        expect(['completed', 'failed']).toContain(status)
      }
    }
  })

  it('showPlanConfirm only on planned', () => {
    for (const [status, row] of Object.entries(MATRIX)) {
      expect(row.showPlanConfirm).toBe(status === 'planned')
    }
  })

  it('isActive implies canStopHeader (active tasks must be stoppable)', () => {
    for (const [, row] of Object.entries(MATRIX)) {
      if (row.isActive) expect(row.canStopHeader).toBe(true)
    }
  })

  it('MATRIX covers exactly the 8 known statuses', () => {
    expect(new Set(Object.keys(MATRIX))).toEqual(
      new Set([
        'pending',
        'queued',
        'designing',
        'planned',
        'running',
        'waiting',
        'completed',
        'failed',
      ]),
    )
  })
})

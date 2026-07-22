/**
 * Contract test: submitCreateTask must reset the same UI state
 * variables as selectTask.
 *
 * Regression guard for the bug where creating a new task showed
 * stale conversationItems/agentMessages from the previously
 * selected task because submitCreateTask forgot to clear them.
 *
 * This test reads App.tsx source and extracts the set* calls from
 * both functions, then asserts submitCreateTask is a superset of
 * the state resets in selectTask.
 */
import { describe, it, expect } from 'vitest'
import fs from 'fs'
import path from 'path'

/**
 * Extract all `setState(...)` / `setState([])` / `setState({})` /
 * `setState(null)` / `setState('')` reset calls from a code block.
 * These are the "clear to default" calls that reset UI state.
 */
function extractResetCalls(code: string): Set<string> {
  // Match setter calls that reset to empty/null/default values:
  //   setFoo([])  setFoo({})  setFoo(null)  setFoo('')
  const pattern = /\b(set[A-Z]\w+)\(\s*(?:\[\]|\{\}|null|''|"")\s*\)/g
  const calls = new Set<string>()
  let m
  while ((m = pattern.exec(code)) !== null) {
    calls.add(m[1])
  }
  return calls
}

/**
 * Extract the body of a function defined as `const name = async (...) => {`
 * with brace-matching to handle nested blocks.
 */
function extractFunctionBody(source: string, name: string): string {
  const startPattern = new RegExp(`const\\s+${name}\\s*=`)
  const match = startPattern.exec(source)
  if (!match) throw new Error(`Function ${name} not found in source`)

  // Find first '{' after the match
  const braceStart = source.indexOf('{', match.index)
  if (braceStart === -1) throw new Error(`No opening brace for ${name}`)

  let depth = 0
  let i = braceStart
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++
    else if (source[i] === '}') {
      depth--
      if (depth === 0) break
    }
  }

  return source.slice(braceStart, i + 1)
}

describe('submitCreateTask state reset contract', () => {
  const appSource = fs.readFileSync(
    path.resolve(__dirname, '../App.tsx'),
    'utf-8',
  )

  // The task loader (opening a task now navigates; the route effect
  // calls loadTaskById, which holds the per-task state resets).
  const selectTaskBody = extractFunctionBody(appSource, 'loadTaskById')
  const submitCreateBody = extractFunctionBody(appSource, 'submitCreateTask')

  const selectTaskResets = extractResetCalls(selectTaskBody)
  const submitCreateResets = extractResetCalls(submitCreateBody)

  // These are set by loadTaskById with loaded data, not reset to empty.
  // submitCreateTask doesn't need them since a new task has no data.
  const loadOnlyCalls = new Set([
    'setSelectedTask',  // set with new task object, not cleared
    'setSteps',         // loadTaskById loads from API; submitCreateTask clears
  ])

  const requiredResets = new Set(
    [...selectTaskResets].filter(s => !loadOnlyCalls.has(s)),
  )

  it('loadTaskById should have state resets (sanity check)', () => {
    expect(selectTaskResets.size).toBeGreaterThan(5)
  })

  for (const setter of requiredResets) {
    it(`submitCreateTask must reset ${setter}`, () => {
      expect(
        submitCreateResets.has(setter),
        `submitCreateTask is missing ${setter}() reset — ` +
          `selectTask clears it but submitCreateTask does not. ` +
          `New tasks will show stale state from the previous task.`,
      ).toBe(true)
    })
  }
})

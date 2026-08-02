// The console is reachable by URL so a reviewer can bookmark it, reload
// without losing their place, and send the link on. These pin the mapping
// in both task shapes — flat (no-store) and nested under a store.

import { describe, expect, it } from 'vitest'
import { parseNav } from '../lib/route'

describe('audit console route', () => {
  it('opens for a no-store task', () => {
    const nav = parseNav('/tasks/abc123/audit')
    expect(nav.auditOpen).toBe(true)
    expect(nav.taskId).toBe('abc123')
    expect(nav.storeId).toBeNull()
  })

  it('opens for a store-scoped task, keeping the store selected', () => {
    const nav = parseNav('/stores/s1/tasks/abc123/audit')
    expect(nav.auditOpen).toBe(true)
    expect(nav.taskId).toBe('abc123')
    // The store stays selected behind the console — reloading the link
    // must not lose which store the reviewer was in.
    expect(nav.storeId).toBe('s1')
    expect(nav.appView).toBe('tasks')
  })

  it('tolerates a trailing slash, as a pasted link often has one', () => {
    expect(parseNav('/tasks/abc123/audit/').auditOpen).toBe(true)
  })

  it('is closed on the task itself', () => {
    expect(parseNav('/tasks/abc123').auditOpen).toBe(false)
    expect(parseNav('/stores/s1/tasks/abc123').auditOpen).toBe(false)
  })

  it('does not open on unrelated paths', () => {
    for (const p of ['/tasks', '/workspace', '/settings/stores', '/schedules']) {
      expect(parseNav(p).auditOpen).toBe(false)
    }
  })

  it('does not open on a deeper path that merely starts with audit', () => {
    // Guards against a loose prefix match claiming a future sub-route.
    expect(parseNav('/tasks/abc123/audit/row/7').auditOpen).toBe(false)
  })
})

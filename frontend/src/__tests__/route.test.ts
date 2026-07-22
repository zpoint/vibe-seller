/**
 * Unit tests for the URL ↔ navigation-state mapping (lib/route).
 * Pins the route contract the router + App depend on: which view a path
 * shows, the selected ids it deep-links, and the settings slug↔id map.
 */
import { describe, it, expect } from 'vitest'
import {
  parseNav,
  settingsTabToSlug,
  slugToSettingsTab,
} from '../lib/route'

describe('parseNav — top-level view', () => {
  it('defaults to tasks', () => {
    expect(parseNav('/tasks').appView).toBe('tasks')
    expect(parseNav('/').appView).toBe('tasks')
  })
  it('maps /workspace and /settings', () => {
    expect(parseNav('/workspace').appView).toBe('workspace')
    expect(parseNav('/settings/ai').appView).toBe('settings')
  })
  it('schedules stay under the tasks view, scheduled subtab', () => {
    expect(parseNav('/schedules').appView).toBe('tasks')
    expect(parseNav('/schedules').taskSubTab).toBe('scheduled')
    expect(parseNav('/tasks').taskSubTab).toBe('onetime')
  })
})

describe('parseNav — selection params', () => {
  it('extracts the open task / store / schedule id', () => {
    expect(parseNav('/tasks/abc-123').taskId).toBe('abc-123')
    expect(parseNav('/stores/s1').storeId).toBe('s1')
    expect(parseNav('/schedules/sch9').scheduleId).toBe('sch9')
  })
  it('leaves ids null when absent', () => {
    const n = parseNav('/tasks')
    expect(n.taskId).toBeNull()
    expect(n.storeId).toBeNull()
    expect(n.scheduleId).toBeNull()
  })
  it('a flat task path (no-store task) sets no store id', () => {
    expect(parseNav('/tasks/t1').storeId).toBeNull()
    expect(parseNav('/tasks/t1').taskId).toBe('t1')
  })
  it('a store-scoped task path sets both store and task id', () => {
    const n = parseNav('/stores/s1/tasks/t1')
    expect(n.storeId).toBe('s1')
    expect(n.taskId).toBe('t1')
    expect(n.appView).toBe('tasks')
    expect(n.taskSubTab).toBe('onetime')
  })
})

describe('settings slug ↔ tab id', () => {
  it('maps the AI tab both ways', () => {
    expect(parseNav('/settings/ai').settingsTab).toBe('aiAgent')
    expect(settingsTabToSlug('aiAgent')).toBe('ai')
    expect(slugToSettingsTab('ai')).toBe('aiAgent')
  })
  it('passes other tabs through unchanged', () => {
    expect(parseNav('/settings/email').settingsTab).toBe('email')
    expect(settingsTabToSlug('integrations')).toBe('integrations')
  })
  it('defaults to the stores tab', () => {
    expect(parseNav('/settings').settingsTab).toBe('stores')
  })
})

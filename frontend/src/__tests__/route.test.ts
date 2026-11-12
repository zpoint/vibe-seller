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
  listTarget,
  scheduleTarget,
} from '../lib/route'

describe('parseNav — top-level view', () => {
  it('defaults to tasks', () => {
    expect(parseNav('/tasks').appView).toBe('tasks')
    expect(parseNav('/').appView).toBe('tasks')
  })
  it('only the bare / is the unresolved landing', () => {
    expect(parseNav('/').landing).toBe(true)
    expect(parseNav('/tasks').landing).toBe(false)
    expect(parseNav('/schedules').landing).toBe(false)
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
  it('a store-scoped schedule LIST keeps the store and the scheduled tab', () => {
    const n = parseNav('/stores/s1/schedules')
    expect(n.storeId).toBe('s1')
    expect(n.scheduleId).toBeNull()
    expect(n.taskSubTab).toBe('scheduled')
    expect(n.appView).toBe('tasks')
  })
  it('a store-scoped schedule DETAIL parses store + schedule id', () => {
    const n = parseNav('/stores/s1/schedules/sch9')
    expect(n.storeId).toBe('s1')
    expect(n.scheduleId).toBe('sch9')
    expect(n.taskSubTab).toBe('scheduled')
    expect(n.taskId).toBeNull()
  })
})

describe('scope targets (URL builders for scope × sub-tab navigation)', () => {
  it('listTarget: the four list URLs', () => {
    expect(listTarget(null, false)).toEqual({ to: '/tasks' })
    expect(listTarget(null, true)).toEqual({ to: '/schedules' })
    expect(listTarget('s1', false)).toEqual({ to: '/stores/$storeId', params: { storeId: 's1' } })
    expect(listTarget('s1', true)).toEqual({ to: '/stores/$storeId/schedules', params: { storeId: 's1' } })
  })
  it('scheduleTarget: detail and leave-to-list, per scope', () => {
    expect(scheduleTarget(null, 'x1')).toEqual({ to: '/schedules/$scheduleId', params: { scheduleId: 'x1' } })
    expect(scheduleTarget('s1', 'x1')).toEqual({
      to: '/stores/$storeId/schedules/$scheduleId', params: { storeId: 's1', scheduleId: 'x1' },
    })
    expect(scheduleTarget('s1', null)).toEqual(listTarget('s1', true))
    expect(scheduleTarget(null, null)).toEqual(listTarget(null, true))
  })
})

describe('settings slug ↔ tab id', () => {
  it('maps the AI tab both ways', () => {
    expect(parseNav('/settings/ai').settingsTab).toBe('aiAgent')
    expect(settingsTabToSlug('aiAgent')).toBe('ai')
    expect(slugToSettingsTab('ai')).toBe('aiAgent')
  })
  it('redirects the retired API-tokens tab to Integrations', () => {
    expect(parseNav('/settings/api-tokens').settingsTab).toBe('integrations')
    expect(slugToSettingsTab('api-tokens')).toBe('integrations')
  })
  it('passes other tabs through unchanged', () => {
    expect(parseNav('/settings/email').settingsTab).toBe('email')
    expect(settingsTabToSlug('integrations')).toBe('integrations')
  })
  it('defaults to the stores tab', () => {
    expect(parseNav('/settings').settingsTab).toBe('stores')
  })
})

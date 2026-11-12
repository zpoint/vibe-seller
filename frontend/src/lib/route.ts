/**
 * Pure URL ↔ navigation-state mapping for the app router.
 *
 * The router keeps `App` mounted and derives what's shown from the URL;
 * this module is the single, testable place that maps a pathname to the
 * active view / settings tab / selected ids, and back. Kept free of React
 * so it can be unit-tested directly (see __tests__/route.test.ts).
 */
import type { AppView } from '../types'
import type { SettingsTab } from '../views/SettingsView'

// The settings URL slug is the human-friendly form; SettingsView's
// internal id differs from it for the AI tab ('ai' ↔ 'aiAgent'). The
// retired API-tokens tab has no id any more — its old slug redirects
// to Integrations, where the API-key card now lives.
const SLUG_TO_TAB: Record<string, SettingsTab> = {
  ai: 'aiAgent',
  'api-tokens': 'integrations',
}
const TAB_TO_SLUG: Partial<Record<SettingsTab, string>> = {
  aiAgent: 'ai',
}

export function settingsTabToSlug(tab: SettingsTab): string {
  return TAB_TO_SLUG[tab] || tab
}
export function slugToSettingsTab(slug: string): SettingsTab {
  return (SLUG_TO_TAB[slug] || slug) as SettingsTab
}

export interface NavState {
  appView: AppView
  settingsTab: SettingsTab
  taskId: string | null
  storeId: string | null
  scheduleId: string | null
  taskSubTab: 'onetime' | 'scheduled'
  // The bare landing URL '/': no scope is active until App resolves it
  // (first store, else /tasks) once the store list arrives.
  landing: boolean
}

// Both the task list and the schedule views exist in a flat (all-stores)
// and a store-nested form (/stores/$storeId/…); the nested form is how
// the URL keeps its scope — switching sub-tabs or opening a schedule
// inside a store must not drop the store. These helpers are that rule in
// one place; App navigates through them.
export function listTarget(storeId: string | null, scheduled: boolean) {
  if (storeId) {
    return scheduled
      ? { to: '/stores/$storeId/schedules' as const, params: { storeId } }
      : { to: '/stores/$storeId' as const, params: { storeId } }
  }
  return { to: scheduled ? '/schedules' as const : '/tasks' as const }
}
export function scheduleTarget(storeId: string | null, scheduleId: string | null) {
  if (!scheduleId) return listTarget(storeId, true)
  if (storeId) {
    return { to: '/stores/$storeId/schedules/$scheduleId' as const, params: { storeId, scheduleId } }
  }
  return { to: '/schedules/$scheduleId' as const, params: { scheduleId } }
}

const STORE_PREFIX = /^\/stores\/([^/]+)/
const TASK_PATH = /^\/(?:stores\/[^/]+\/)?tasks\/([^/]+)/
const SCHEDULE_LIST = /^\/(?:stores\/[^/]+\/)?schedules(\/|$)/
const SCHEDULE_PATH = /^\/(?:stores\/[^/]+\/)?schedules\/([^/]+)/

export function parseNav(pathname: string): NavState {
  const appView: AppView = pathname.startsWith('/workspace')
    ? 'workspace'
    : pathname.startsWith('/settings')
      ? 'settings'
      : 'tasks'
  const tabSlug = pathname.match(/^\/settings\/([^/]+)/)?.[1] || 'stores'
  return {
    appView,
    settingsTab: slugToSettingsTab(tabSlug),
    // Task and schedule paths come in a flat and a store-nested form
    // (/stores/$storeId/tasks|schedules/$id) — the nested form keeps the
    // store selected while the detail or list is open. The store id
    // comes from the /stores/ prefix, present only in the nested form.
    taskId: pathname.match(TASK_PATH)?.[1] ?? null,
    storeId: pathname.match(STORE_PREFIX)?.[1] ?? null,
    scheduleId: pathname.match(SCHEDULE_PATH)?.[1] ?? null,
    taskSubTab: SCHEDULE_LIST.test(pathname) ? 'scheduled' : 'onetime',
    landing: pathname === '/',
  }
}

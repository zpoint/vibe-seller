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
// internal id differs only for the AI tab ('ai' ↔ 'aiAgent').
const SLUG_TO_TAB: Record<string, SettingsTab> = { ai: 'aiAgent' }
const TAB_TO_SLUG: Partial<Record<SettingsTab, string>> = { aiAgent: 'ai' }

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
}

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
    // A task path is either flat (/tasks/$id, no-store tasks) or nested
    // under its store (/stores/$storeId/tasks/$id) — the nested form keeps
    // the store selected while the task is open. Match both; the store id
    // comes from the /stores/ prefix, present only in the nested form.
    taskId: pathname.match(/^\/(?:stores\/[^/]+\/)?tasks\/([^/]+)/)?.[1] ?? null,
    storeId: pathname.match(/^\/stores\/([^/]+)/)?.[1] ?? null,
    scheduleId: pathname.match(/^\/schedules\/([^/]+)/)?.[1] ?? null,
    taskSubTab: pathname.startsWith('/schedules') ? 'scheduled' : 'onetime',
  }
}

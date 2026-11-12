/**
 * Client-side route tree (TanStack Router).
 *
 * `App` is the root-route component: it stays mounted across every
 * navigation (so SSE, selected-task state, etc. are preserved) and
 * derives the active view / settings tab from the URL instead of
 * `useState`. Leaf routes exist to make paths + params first-class
 * (type-safe `navigate`, no notFound on deep-links); their rendering is
 * still done by `App`, so they carry no component.
 *
 * Paths: /tasks · /tasks/$taskId · /stores/$storeId ·
 * /stores/$storeId/tasks/$taskId · /schedules · /schedules/$scheduleId ·
 * /stores/$storeId/schedules · /stores/$storeId/schedules/$scheduleId ·
 * /workspace · /settings/$tab. `/settings` redirects to a sensible
 * default. `/` is a real route — the landing: App replaces it with the
 * first store (or /tasks when there are none) once the store list
 * arrives, and only while the live URL is still exactly `/`, so a click
 * or deep link made meanwhile is never overridden.
 * Scope lives in the path wherever it applies: a store-scoped task or
 * schedule view lives under its store so opening it keeps the store
 * selected (the flat forms are for the all-stores view and no-store
 * tasks).
 *
 * Routes are declared inline (not via a helper) so TanStack can infer
 * each literal path into the type-safe `navigate`/`redirect` surface.
 */
import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from '@tanstack/react-router'
import App from './App'

const rootRoute = createRootRoute({ component: App })

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  // Landing: no synchronous redirect (it would send everyone to All
  // Stores before the store list is even known). App resolves '/' when
  // /api/stores arrives — see the landing effect.
  component: () => null,
})
const tasksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'tasks',
  component: () => null,
})
const taskRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'tasks/$taskId',
  component: () => null,
})
const storeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'stores/$storeId',
  component: () => null,
})
const storeTaskRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'stores/$storeId/tasks/$taskId',
  component: () => null,
})
const storeSchedulesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'stores/$storeId/schedules',
  component: () => null,
})
const storeScheduleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'stores/$storeId/schedules/$scheduleId',
  component: () => null,
})
const schedulesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'schedules',
  component: () => null,
})
const scheduleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'schedules/$scheduleId',
  component: () => null,
})
const workspaceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'workspace',
  component: () => null,
})
const settingsIndexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'settings',
  beforeLoad: () => {
    throw redirect({ to: '/settings/$tab', params: { tab: 'stores' } })
  },
})
const settingsTabRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'settings/$tab',
  // The API-tokens tab was retired: its card moved into Integrations,
  // so the old slug redirects there and anchors to the card.
  beforeLoad: ({ params }) => {
    if (params.tab === 'api-tokens') {
      throw redirect({
        to: '/settings/$tab',
        params: { tab: 'integrations' },
        hash: 'api-keys',
      })
    }
  },
  component: () => null,
})

const routeTree = rootRoute.addChildren([
  indexRoute,
  tasksRoute,
  taskRoute,
  storeRoute,
  storeTaskRoute,
  storeSchedulesRoute,
  storeScheduleRoute,
  schedulesRoute,
  scheduleRoute,
  workspaceRoute,
  settingsIndexRoute,
  settingsTabRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

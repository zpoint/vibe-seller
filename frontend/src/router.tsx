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
 * /workspace · /settings/$tab. `/` and `/settings` redirect to sensible
 * defaults. A store-scoped task lives under its store so opening it keeps
 * the store selected (the flat /tasks/$taskId form is for no-store tasks).
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
  beforeLoad: () => {
    throw redirect({ to: '/tasks' })
  },
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
  component: () => null,
})

const routeTree = rootRoute.addChildren([
  indexRoute,
  tasksRoute,
  taskRoute,
  storeRoute,
  storeTaskRoute,
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

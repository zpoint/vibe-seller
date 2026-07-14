/**
 * Tests for AllStoresTaskList — grouped child tasks display for
 * all-stores schedules.
 *
 * These tests import the REAL component from components/AllStoresTaskList.tsx
 * so they catch regressions when the component is modified.
 *
 * Scenarios covered:
 * 1. Specific store selected → current store expanded first, others collapsed
 * 2. All-tasks view → all groups collapsed by default, click to expand
 * 3. Expand/collapse toggle for other stores
 * 4. Sorting: current store first, then alphabetical
 * 5. Task click fires selectTask
 * 6. Edge cases: orphaned tasks, empty list, deleted stores, multiple tasks per store
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { i18nTestInstance, makeTask } from '../test/helpers'
import { AllStoresTaskList } from '../components/AllStoresTaskList'
import type { Store } from '../types'

// ── Test factories ───────────────────────────────────────

const makeStore = (overrides: Partial<Store> = {}): Store => ({
  id: `store-${Math.random().toString(36).slice(2, 6)}`,
  name: 'Test Store',
  browser_backend: 'chrome',
  browser_config: {},
  ziniao_account_id: null,
  browser_oauth: null,
  platforms: [],
  countries: [],
  platform_countries: {},
  created_at: '',
  updated_at: '',
  ...overrides,
})

function renderList(overrides: {
  scheduleTasks?: Parameters<typeof AllStoresTaskList>[0]['scheduleTasks']
  stores?: Store[]
  selectedStore?: Store | null
  showAllTasks?: boolean
}) {
  const stores = overrides.stores || [
    makeStore({ id: 'store-a', name: 'Amazon US' }),
    makeStore({ id: 'store-b', name: 'Amazon UK' }),
  ]
  const selectTask = vi.fn()
  const formatDate = (d: string) => new Date(d).toLocaleDateString()

  const result = render(
    <I18nextProvider i18n={i18nTestInstance}>
      <AllStoresTaskList
        scheduleTasks={overrides.scheduleTasks || []}
        stores={stores}
        selectedStore={overrides.selectedStore ?? null}
        showAllTasks={overrides.showAllTasks ?? false}
        selectTask={selectTask}
        formatDate={formatDate}
      />
    </I18nextProvider>,
  )

  return { ...result, selectTask }
}

// ── Tests ────────────────────────────────────────────────

describe('AllStoresTaskList', () => {
  describe('store-focused mode (specific store selected, showAllTasks=false)', () => {
    it('shows current store group first with blue highlight, tasks visible', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Catalog Sync A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Catalog Sync B' }),
      ]

      const { container } = renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        selectedStore: storeA,
        showAllTasks: false,
      })

      // Current store header is blue (not a button)
      const blueHeader = container.querySelector('.bg-indigo-50')
      expect(blueHeader).toBeInTheDocument()
      expect(blueHeader?.textContent).toBe('Amazon US')

      // Current store's task is visible
      expect(screen.getByText('Catalog Sync A')).toBeInTheDocument()

      // Other store is NOT expanded by default — its task is hidden
      expect(screen.queryByText('Catalog Sync B')).not.toBeInTheDocument()
    })

    it('shows other stores collapsed with task count', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Task B1' }),
        makeTask({ id: 't3', store_id: 'store-b', title: 'Task B2' }),
      ]

      renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        selectedStore: storeA,
        showAllTasks: false,
      })

      // Other store header shows as button with count
      expect(screen.getByText('Amazon UK')).toBeInTheDocument()
      expect(screen.getByText('(2)')).toBeInTheDocument()
    })

    it('expands other store on click and shows its tasks', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Task B' }),
      ]

      renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        selectedStore: storeA,
        showAllTasks: false,
      })

      // Other store's task hidden initially
      expect(screen.queryByText('Task B')).not.toBeInTheDocument()

      // Click the other store header to expand
      fireEvent.click(screen.getByText('Amazon UK'))

      // Now the task is visible
      expect(screen.getByText('Task B')).toBeInTheDocument()
    })

    it('collapses other store on second click', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Task B' }),
      ]

      renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        selectedStore: storeA,
        showAllTasks: false,
      })

      // Expand
      fireEvent.click(screen.getByText('Amazon UK'))
      expect(screen.getByText('Task B')).toBeInTheDocument()

      // Collapse
      fireEvent.click(screen.getByText('Amazon UK'))
      expect(screen.queryByText('Task B')).not.toBeInTheDocument()
    })

    it('sorts current store first, others alphabetical', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Zebra Store' })
      const storeB = makeStore({ id: 'store-b', name: 'Alpha Store' })
      const storeC = makeStore({ id: 'store-c', name: 'Middle Store' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-b', title: 'Alpha Task' }),
        makeTask({ id: 't2', store_id: 'store-c', title: 'Middle Task' }),
        makeTask({ id: 't3', store_id: 'store-a', title: 'Zebra Task' }),
      ]

      const { container } = renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB, storeC],
        selectedStore: storeA, // Zebra Store is current
        showAllTasks: false,
      })

      // Blue header = current store (Zebra)
      const blueHeader = container.querySelector('.bg-indigo-50')
      expect(blueHeader?.textContent).toBe('Zebra Store')

      // Other stores in alpha order: Alpha, Middle
      const buttons = container.querySelectorAll('button')
      const storeNames = Array.from(buttons)
        .map(b => {
          const m = b.textContent?.match(/▶\s*(.+?)\s*\(\d+\)/)
          return m ? m[1].trim() : null
        })
        .filter(Boolean)
      expect(storeNames[0]).toBe('Alpha Store')
      expect(storeNames[1]).toBe('Middle Store')
    })
  })

  describe('all-tasks mode (showAllTasks=true)', () => {
    it('shows all store groups collapsed by default with toggle headers', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Task B' }),
      ]

      const { container } = renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        showAllTasks: true,
      })

      // All tasks hidden — collapsed by default
      expect(screen.queryByText('Task A')).not.toBeInTheDocument()
      expect(screen.queryByText('Task B')).not.toBeInTheDocument()

      // No blue highlight (no focused store)
      expect(container.querySelector('.bg-indigo-50')).not.toBeInTheDocument()

      // Headers are toggle buttons
      const toggleButtons = container.querySelectorAll('button[aria-expanded]')
      expect(toggleButtons.length).toBe(2)
    })

    it('expands group on click in all-tasks mode', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
      ]

      renderList({
        scheduleTasks: tasks,
        stores: [storeA],
        showAllTasks: true,
      })

      // Initially collapsed
      expect(screen.queryByText('Task A')).not.toBeInTheDocument()

      // Click to expand
      fireEvent.click(screen.getByText('Amazon US'))
      expect(screen.getByText('Task A')).toBeInTheDocument()
    })

    it('sorts groups alphabetically when no store selected', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Zebra Store' })
      const storeB = makeStore({ id: 'store-b', name: 'Alpha Store' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Z Task' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'A Task' }),
      ]

      const { container } = renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        showAllTasks: true,
      })

      // Alpha Store group comes before Zebra Store group
      const groupDivs = container.querySelectorAll('.bg-gray-50')
      const names = Array.from(groupDivs).map(el => {
        return el.textContent?.replace(/▶/g, '').replace(/\(\d+\)/, '').trim() || ''
      })
      expect(names[0]).toBe('Alpha Store')
      expect(names[1]).toBe('Zebra Store')
    })

    it('sorts selected store first even in all-tasks mode', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Task B' }),
      ]

      const { container } = renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        selectedStore: storeA,
        showAllTasks: true,
      })

      // No blue highlight — all-tasks mode doesn't focus a store
      expect(container.querySelector('.bg-indigo-50')).not.toBeInTheDocument()

      // Selected store sorts first
      const groupDivs = container.querySelectorAll('.bg-gray-50')
      const names = Array.from(groupDivs).map(el => {
        return el.textContent?.replace(/▶/g, '').replace(/\(\d+\)/, '').trim() || ''
      })
      expect(names[0]).toBe('Amazon US')
      expect(names[1]).toBe('Amazon UK')
    })
  })

  describe('task interaction', () => {
    it('fires selectTask when clicking a visible task', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const task = makeTask({ id: 't1', store_id: 'store-a', title: 'Sync Catalog' })

      const { selectTask } = renderList({
        scheduleTasks: [task],
        stores: [storeA],
        selectedStore: storeA,
        showAllTasks: false,
      })

      fireEvent.click(screen.getByText('Sync Catalog'))
      expect(selectTask).toHaveBeenCalledWith(task)
    })
  })

  describe('accessibility', () => {
    it('toggle buttons have aria-expanded attribute', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Task B' }),
      ]

      const { container } = renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        selectedStore: storeA,
        showAllTasks: false,
      })

      const toggleButton = container.querySelector('button[aria-expanded]')
      expect(toggleButton).toBeInTheDocument()
      expect(toggleButton?.getAttribute('aria-expanded')).toBe('false')
    })

    it('aria-expanded updates on toggle', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const storeB = makeStore({ id: 'store-b', name: 'Amazon UK' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'Task A' }),
        makeTask({ id: 't2', store_id: 'store-b', title: 'Task B' }),
      ]

      const { container } = renderList({
        scheduleTasks: tasks,
        stores: [storeA, storeB],
        selectedStore: storeA,
        showAllTasks: false,
      })

      const toggleButton = container.querySelector('button[aria-expanded]')
      expect(toggleButton?.getAttribute('aria-expanded')).toBe('false')

      fireEvent.click(toggleButton!)
      expect(toggleButton?.getAttribute('aria-expanded')).toBe('true')
    })
  })

  describe('edge cases', () => {
    it('handles tasks with no store_id (orphaned tasks)', () => {
      const tasks = [
        makeTask({ id: 't1', store_id: null, title: 'Orphan Task' }),
      ]

      renderList({ scheduleTasks: tasks, showAllTasks: true })

      // System group header visible (collapsed)
      expect(screen.getByText('System')).toBeInTheDocument()

      // Expand to see the task
      fireEvent.click(screen.getByText('System'))
      expect(screen.getByText('Orphan Task')).toBeInTheDocument()
    })

    it('handles empty task list', () => {
      const { container } = renderList({ scheduleTasks: [], showAllTasks: true })
      expect(container.querySelector('.bg-gray-50')).not.toBeInTheDocument()
      expect(container.querySelector('.bg-indigo-50')).not.toBeInTheDocument()
    })

    it('handles tasks for a store not in the stores list', () => {
      const tasks = [
        makeTask({ id: 't1', store_id: 'deleted-store', title: 'Ghost Task' }),
      ]

      renderList({ scheduleTasks: tasks, showAllTasks: true })

      // Falls back to store ID as name
      expect(screen.getByText('deleted-store')).toBeInTheDocument()

      // Expand to see the task
      fireEvent.click(screen.getByText('deleted-store'))
      expect(screen.getByText('Ghost Task')).toBeInTheDocument()
    })

    it('groups multiple tasks under same store', () => {
      const storeA = makeStore({ id: 'store-a', name: 'Amazon US' })
      const tasks = [
        makeTask({ id: 't1', store_id: 'store-a', title: 'First Run' }),
        makeTask({ id: 't2', store_id: 'store-a', title: 'Second Run' }),
        makeTask({ id: 't3', store_id: 'store-a', title: 'Third Run' }),
      ]

      renderList({
        scheduleTasks: tasks,
        stores: [storeA],
        showAllTasks: true,
      })

      // Count badge shows (3)
      expect(screen.getByText('(3)')).toBeInTheDocument()

      // Expand to see all tasks
      fireEvent.click(screen.getByText('Amazon US'))
      expect(screen.getByText('First Run')).toBeInTheDocument()
      expect(screen.getByText('Second Run')).toBeInTheDocument()
      expect(screen.getByText('Third Run')).toBeInTheDocument()
    })
  })
})

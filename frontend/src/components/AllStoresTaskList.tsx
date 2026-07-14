import { useMemo, useState } from 'react'
import { StatusBadge } from './ui'
import type { Task, Store } from '../types'

interface AllStoresTaskListProps {
  scheduleTasks: Task[]
  stores: Store[]
  selectedStore: Store | null
  showAllTasks: boolean
  selectTask: (task: Task) => void
  formatDate: (d: string) => string
}

/** Renders grouped child tasks for all-stores schedules.
 *  - Specific store selected: current store expanded, others collapsed
 *  - "All Tasks" view: all groups collapsed by default, click to expand */
export function AllStoresTaskList({
  scheduleTasks,
  stores,
  selectedStore,
  showAllTasks,
  selectTask,
  formatDate,
}: AllStoresTaskListProps) {
  const [expandedStores, setExpandedStores] = useState<Set<string>>(new Set())
  const storeNameMap = useMemo(
    () => new Map(stores.map(s => [s.id, s.name])),
    [stores],
  )

  // Group tasks by store (keyed by stable storeId)
  const grouped = useMemo(() => {
    const result: { storeKey: string; storeName: string; tasks: Task[] }[] = []
    const map = new Map<string, Task[]>()
    for (const task of scheduleTasks) {
      const key = task.store_id || '__none__'
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(task)
    }
    for (const [key, tasks] of map) {
      const name = key === '__none__' ? 'System' : (storeNameMap.get(key) || key)
      result.push({ storeKey: key, storeName: name, tasks })
    }
    return result
  }, [scheduleTasks, storeNameMap])

  // Sort: current store first, then alphabetical
  const sorted = useMemo(() => {
    const arr = [...grouped]
    const currentId = selectedStore?.id
    arr.sort((a, b) => {
      if (a.storeKey === currentId) return -1
      if (b.storeKey === currentId) return 1
      return a.storeName.localeCompare(b.storeName)
    })
    return arr
  }, [grouped, selectedStore])

  const toggle = (storeKey: string) => {
    setExpandedStores(prev => {
      const next = new Set(prev)
      if (next.has(storeKey)) next.delete(storeKey)
      else next.add(storeKey)
      return next
    })
  }

  const isStoreFocused = selectedStore && !showAllTasks

  return (
    <>
      {sorted.map(({ storeKey, storeName, tasks: grpTasks }) => {
        const isCurrent = isStoreFocused && storeKey === selectedStore?.id
        const isExpanded = isCurrent || expandedStores.has(storeKey)

        return (
          <div key={storeKey}>
            {isStoreFocused && isCurrent ? (
              <div className="px-2 py-1 text-xs font-medium text-indigo-600 bg-indigo-50 rounded">{storeName}</div>
            ) : (
              <button
                type="button"
                onClick={() => toggle(storeKey)}
                aria-expanded={isExpanded}
                className="w-full flex items-center gap-1.5 px-2 py-1 text-xs font-medium text-gray-500 bg-gray-50 rounded hover:bg-gray-100 transition-colors"
              >
                <span className={`transition-transform ${isExpanded ? 'rotate-90' : ''}`}>▶</span>
                <span>{storeName}</span>
                <span className="text-gray-400">({grpTasks.length})</span>
              </button>
            )}
            {isExpanded && grpTasks.map(task => (
              <button
                key={task.id}
                onClick={() => selectTask(task)}
                className="w-full text-left bg-white rounded-lg border border-gray-200 p-3 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm truncate">{task.title}</span>
                  <StatusBadge status={task.status} />
                </div>
                <div className="text-xs text-gray-400 mt-1">{formatDate(task.created_at)}</div>
              </button>
            ))}
          </div>
        )
      })}
    </>
  )
}

import { describe, it, expect } from 'vitest'

// Simple state management test (mimicking zustand behavior)
interface Task {
  id: string
  title: string
  status: string
}

interface TaskStore {
  tasks: Task[]
  selectedTask: Task | null
  setTasks: (tasks: Task[]) => void
  addTask: (task: Task) => void
  selectTask: (task: Task | null) => void
  updateTask: (id: string, updates: Partial<Task>) => void
}

function createTaskStore(): TaskStore {
  const state = {
    tasks: [] as Task[],
    selectedTask: null as Task | null,
  }

  return {
    get tasks() { return state.tasks },
    get selectedTask() { return state.selectedTask },

    setTasks(tasks: Task[]) {
      state.tasks = tasks
    },

    addTask(task: Task) {
      state.tasks = [...state.tasks, task]
    },

    selectTask(task: Task | null) {
      state.selectedTask = task
    },

    updateTask(id: string, updates: Partial<Task>) {
      state.tasks = state.tasks.map(t =>
        t.id === id ? { ...t, ...updates } : t
      )
      if (state.selectedTask?.id === id) {
        state.selectedTask = { ...state.selectedTask, ...updates }
      }
    },
  }
}

describe('Task Store', () => {
  it('should initialize with empty state', () => {
    const store = createTaskStore()

    expect(store.tasks).toEqual([])
    expect(store.selectedTask).toBeNull()
  })

  it('should set tasks', () => {
    const store = createTaskStore()
    const tasks: Task[] = [
      { id: '1', title: 'Task 1', status: 'pending' },
      { id: '2', title: 'Task 2', status: 'running' },
    ]

    store.setTasks(tasks)

    expect(store.tasks).toHaveLength(2)
    expect(store.tasks[0].title).toBe('Task 1')
  })

  it('should add a task', () => {
    const store = createTaskStore()
    const task: Task = { id: '1', title: 'New Task', status: 'pending' }

    store.addTask(task)

    expect(store.tasks).toHaveLength(1)
    expect(store.tasks[0]).toEqual(task)
  })

  it('should select a task', () => {
    const store = createTaskStore()
    const task: Task = { id: '1', title: 'Selected Task', status: 'pending' }

    store.selectTask(task)

    expect(store.selectedTask).toEqual(task)
  })

  it('should update a task', () => {
    const store = createTaskStore()
    const task: Task = { id: '1', title: 'Task', status: 'pending' }

    store.addTask(task)
    store.selectTask(task)
    store.updateTask('1', { status: 'completed' })

    expect(store.tasks[0].status).toBe('completed')
    expect(store.selectedTask?.status).toBe('completed')
  })

  it('should update task without affecting selection if different task', () => {
    const store = createTaskStore()
    const task1: Task = { id: '1', title: 'Task 1', status: 'pending' }
    const task2: Task = { id: '2', title: 'Task 2', status: 'running' }

    store.addTask(task1)
    store.addTask(task2)
    store.selectTask(task1)
    store.updateTask('2', { status: 'completed' })

    expect(store.selectedTask?.status).toBe('pending')
    expect(store.tasks.find(t => t.id === '2')?.status).toBe('completed')
  })
})

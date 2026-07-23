/**
 * Create-task with attachments: the task is created with defer_start so it
 * doesn't launch before its files exist; the modal closes immediately;
 * attachments upload in the background; then the task is started. Without
 * files, it launches normally (no defer, no start call).
 */
import { describe, it, expect, vi } from 'vitest'
import { submitCreateTask } from '../handlers/submitCreateTask'
import type { PendingFile, Task } from '../types'

const flush = () => new Promise(r => setTimeout(r, 0))

function deps(over: Record<string, unknown> = {}) {
  const created = { id: 'task-1' } as Task
  const post = vi.fn(async () => created)
  const get = vi.fn(async () => created)
  return {
    created, post, get,
    d: {
      api: { post, get }, storeId: 'store-1', planMode: false,
      setTasks: vi.fn(), setSelectedTask: vi.fn(), onCreated: vi.fn(),
      uploadAttachment: vi.fn(async () => {}),
      startTask: vi.fn(async () => {}),
      ...over,
    },
  }
}

const file = (name: string): PendingFile =>
  ({ id: name, file: new File(['x'], name), preview: '', name }) as PendingFile

describe('submitCreateTask with attachments', () => {
  it('defers start, closes modal, uploads then starts (with files)', async () => {
    const { d, post } = deps()
    await submitCreateTask(
      { title: 't', description: '', files: [file('a.png'), file('b.png')] },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      d as any,
    )
    // Created with defer_start:true
    expect(post).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({ defer_start: true }))
    // Modal closed immediately (before the background upload work).
    expect(d.onCreated).toHaveBeenCalled()
    await flush(); await flush()
    // Both files uploaded, then the task started — start AFTER uploads.
    expect(d.uploadAttachment).toHaveBeenCalledTimes(2)
    expect(d.startTask).toHaveBeenCalledWith('task-1')
    const startOrder = d.startTask.mock.invocationCallOrder[0]
    const lastUpload = Math.max(...d.uploadAttachment.mock.invocationCallOrder)
    expect(startOrder).toBeGreaterThan(lastUpload)
  })

  it('launches normally with no files (no defer, no explicit start)', async () => {
    const { d, post } = deps()
    await submitCreateTask({ title: 't', description: '', files: [] },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      d as any)
    await flush()
    expect(post).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({ defer_start: false }))
    expect(d.uploadAttachment).not.toHaveBeenCalled()
    expect(d.startTask).not.toHaveBeenCalled()
  })
})

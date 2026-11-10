import { useSyncExternalStore } from 'react'
import type { StagedAttachment } from '../types'

/** A send that failed after the user had left its task: what to put
 *  back, and why, when that task's composer is shown again. */
export interface UnsentMessage {
  text: string
  attachments: StagedAttachment[]
  error: string
}

/**
 * The follow-up composer's draft, held outside React state.
 *
 * The app shell owns the draft's lifecycle — it is cleared when a task is
 * opened or created and read when a message is sent — but only the
 * composer displays it. As App state, every keystroke re-rendered the
 * whole app (sidebar, task list, the conversation of a long task); in a
 * store, a keystroke notifies the composer and nothing else. The shell
 * reads it with `get()` at the moment it needs the value.
 */
export interface DraftStore {
  get: () => string
  set: (v: string) => void
  subscribe: (onChange: () => void) => () => void
  /** Keep a failed send for `taskId` until its composer takes it. */
  stash: (taskId: string, msg: UnsentMessage) => void
  /** Take (and forget) the failed send kept for `taskId`. */
  takeStash: (taskId: string) => UnsentMessage | undefined
}

export function createDraftStore(initial = ''): DraftStore {
  let value = initial
  const listeners = new Set<() => void>()
  const unsent = new Map<string, UnsentMessage>()
  return {
    get: () => value,
    set: (v: string) => {
      if (v === value) return
      value = v
      listeners.forEach(l => l())
    },
    subscribe: (l: () => void) => {
      listeners.add(l)
      return () => { listeners.delete(l) }
    },
    stash: (taskId, msg) => { unsent.set(taskId, msg) },
    takeStash: (taskId) => {
      const msg = unsent.get(taskId)
      unsent.delete(taskId)
      return msg
    },
  }
}

/** Subscribe a component to the draft. */
export function useDraft(store: DraftStore): string {
  return useSyncExternalStore(store.subscribe, store.get, store.get)
}

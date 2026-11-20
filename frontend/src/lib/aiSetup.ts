// "Set up AI first": what the set-up-AI dialog and the AI settings
// guide share. A module store, not App state: the dialog lives over the
// task list and the guide on the AI settings tab.
import { useSyncExternalStore } from 'react'
import { api } from '../api'

/** What `GET /api/profiles/readiness` answers (app/ai/ai_readiness.py). */
export interface AiReadiness {
  /** false: show the set-up-AI prompt. */
  ready: boolean
}

/** The docs page for setting up AI, in the UI's language. Same pages the
 *  provider presets link to (mkdocs i18n: zh at the root, en under /en/). */
export function aiDocsUrl(language: string | undefined): string {
  return (language || 'en').startsWith('zh')
    ? 'https://docs.vibe-sellers.com/llm-keys/'
    : 'https://docs.vibe-sellers.com/en/llm-keys/'
}

/** Ask the server. `null` when it could not answer: callers treat that
 *  as "not known to be missing" (never nag on our own failure). */
export async function fetchAiReadiness(): Promise<AiReadiness | null> {
  try {
    const r = await api.get('/api/profiles/readiness')
    return r && typeof r.ready === 'boolean' ? (r as AiReadiness) : null
  } catch {
    return null
  }
}

interface State {
  /** The dialog sent the user to the AI page: the guide opens there. */
  guideRequested: boolean
}

let state: State = { guideRequested: false }
const listeners = new Set<() => void>()

function update(patch: Partial<State>) {
  state = { ...state, ...patch }
  listeners.forEach(l => l())
}

export const aiSetupStore = {
  get: (): State => state,
  subscribe(l: () => void) {
    listeners.add(l)
    return () => { listeners.delete(l) }
  },
  requestGuide: () => update({ guideRequested: true }),
  /** Read-and-reset: true once per request. */
  takeGuideRequest(): boolean {
    if (!state.guideRequested) return false
    update({ guideRequested: false })
    return true
  },
  /** Tests only. */
  reset: () => update({ guideRequested: false }),
}

export function useAiSetupState(): State {
  return useSyncExternalStore(aiSetupStore.subscribe, aiSetupStore.get, aiSetupStore.get)
}

// ─── Request deadline ────────────────────────────────
// A raw `fetch` has no deadline: against a server that accepted the
// connection but never answers (event loop blocked, proxy stalled) it
// stays pending forever, and so does every control awaiting it — a
// send box that never clears its "sending" guard, a task that never
// opens. Every call through `api` therefore carries a deadline and
// rejects with RequestTimeoutError when it passes, so callers can show
// an error instead of hanging. Calls to endpoints that do slow work on
// purpose (git sync, browser launch, integration install) pass a longer
// `timeoutMs` explicitly.
//
// Kept apart from `api.ts` so a test that mocks the api module does not
// also have to re-declare these constants.

export const DEFAULT_TIMEOUT_MS = 30_000
/** For endpoints that legitimately run for minutes. */
export const LONG_TIMEOUT_MS = 5 * 60_000

export interface RequestOptions {
  timeoutMs?: number
}

export class RequestTimeoutError extends Error {
  readonly timeout = true
  constructor(url: string, ms: number) {
    super(`No response from the server after ${Math.round(ms / 1000)}s (${url})`)
    this.name = 'RequestTimeoutError'
  }
}

export const isRequestTimeout = (e: unknown): e is RequestTimeoutError =>
  e instanceof Error && (e as { timeout?: unknown }).timeout === true

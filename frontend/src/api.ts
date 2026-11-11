// ─── API helpers ─────────────────────────────────────
// Centralized fetch wrapper. On 401 we fire a single window-level
// `auth:expired` event so any view/button that hits an expired
// session redirects to the login screen — no per-caller handling
// needed (App.tsx listens once and clears auth state).
import type { ApiToken, CreatedApiToken } from './types'
import {
  DEFAULT_TIMEOUT_MS, RequestTimeoutError, type RequestOptions,
} from './lib/requestDeadline'
import { apiRouteShape, sendEvent } from './lib/telemetry'
import { FrontendEvent } from './lib/telemetryEvents'

export const AUTH_EXPIRED_EVENT = 'auth:expired'

const handleResponse = async (r: Response) => {
  if (r.status === 401) {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw new Error('unauthorized')
  }
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    const detail = err.detail
    // Preserve the structured ``detail`` object so callers can branch
    // on ``err.detail.code`` (e.g. external_config_override modal).
    // Stringify only when ``detail`` is already a string / missing.
    const msg = typeof detail === 'string' && detail
      ? detail
      : `Request failed (${r.status})`
    const e = new Error(msg) as Error & { detail?: unknown; status?: number }
    e.detail = detail
    e.status = r.status
    throw e
  }
  return r.json()
}

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body ? JSON.stringify(body) : undefined,
  credentials: 'include',
})

// Every request carries a deadline — see lib/requestDeadline.ts.
async function request(
  url: string,
  init: RequestInit,
  opts?: RequestOptions,
) {
  const ms = opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const ctrl = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; ctrl.abort() }, ms)
  try {
    // The deadline covers the body too: a server can send headers and
    // then stall mid-body.
    return await handleResponse(await fetch(url, { ...init, signal: ctrl.signal }))
  } catch (e) {
    const status = (e as { status?: number })?.status
    const kind = timedOut ? 'timeout'
      : e instanceof TypeError ? 'network'
        : status !== undefined && status >= 500 ? 'http_5xx' : null
    if (kind) reportFailure(url, init.method ?? 'GET', kind, ms)
    if (timedOut) throw new RequestTimeoutError(url, ms)
    throw e
  } finally {
    clearTimeout(timer)
  }
}

function reportFailure(url: string, method: string, kind: string, ms: number) {
  try {
    const path = new URL(url, window.location.origin).pathname
    if (path.startsWith('/api/telemetry')) return
    sendEvent(FrontendEvent.API_REQUEST_FAILED, {
      path: apiRouteShape(path).split('/').slice(0, 5).join('/'),
      method,
      kind,
      timeout_ms: ms,
    })
  } catch {
    // ignore
  }
}

export const api = {
  get: (url: string, opts?: RequestOptions) =>
    request(url, { credentials: 'include' }, opts),
  post: (url: string, body?: unknown, opts?: RequestOptions) =>
    request(url, jsonInit('POST', body), opts),
  put: (url: string, body?: unknown, opts?: RequestOptions) =>
    request(url, jsonInit('PUT', body), opts),
  patch: (url: string, body?: unknown, opts?: RequestOptions) =>
    request(url, jsonInit('PATCH', body), opts),
  del: (url: string, opts?: RequestOptions) =>
    request(url, { method: 'DELETE', credentials: 'include' }, opts),
}

// ─── API tokens (external-agent access) ───────────────
// Typed wrappers over the generic `api` helper: a caller that mints
// or revokes a token should not have to re-declare the response
// shape, and the shapes live with the rest of the types.
export const apiTokens = {
  /** Tokens owned by the signed-in user. Never includes the
   *  plaintext — only the display prefix. */
  list: (): Promise<ApiToken[]> => api.get('/api/auth/tokens'),
  /** Mint a token. `expiresInDays` null = never expires. The
   *  plaintext `token` is returned by this call and by no other. */
  create: (
    name: string,
    expiresInDays: number | null,
  ): Promise<CreatedApiToken> =>
    api.post('/api/auth/tokens', {
      name,
      expires_in_days: expiresInDays,
    }),
  /** Revoke a token. */
  revoke: (id: string): Promise<void> => api.del(`/api/auth/tokens/${id}`),
}

// ─── Stored-secret reveal (admin only) ────────────────
// Every list/get returns secrets masked, for every caller; this is
// the one endpoint that hands the stored value out — admin session
// only (members and every API token are refused server-side). Used
// by SecretReveal's Show/Copy buttons next to masked fields.
export const revealSecret = (
  resource: string,
  id: string,
  field: string,
): Promise<{ resource: string; id: string; field: string; value: string }> =>
  api.post('/api/secrets/reveal', { resource, id, field })

/**
 * Upload one attachment into a task's workspace.
 *
 * Multipart, so it can't go through `api.post` (which sets a JSON
 * content-type). It still has to REJECT on a non-2xx: a raw `fetch`
 * resolves on 4xx/5xx, and an unchecked upload let the deferred launch
 * proceed with the file missing from the workspace — the agent then asks
 * for an image the user already attached.
 */
export const uploadTaskAttachment = async (taskId: string, file: File) => {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch(`/api/attachments/${taskId}`, { method: 'POST', body: form, credentials: 'include' })
  if (!r.ok) throw new Error(`upload failed (${r.status})`)
}

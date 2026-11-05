// ─── API helpers ─────────────────────────────────────
// Centralized fetch wrapper. On 401 we fire a single window-level
// `auth:expired` event so any view/button that hits an expired
// session redirects to the login screen — no per-caller handling
// needed (App.tsx listens once and clears auth state).
import type { ApiToken, CreatedApiToken } from './types'

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

export const api = {
  get: async (url: string) =>
    handleResponse(await fetch(url, { credentials: 'include' })),
  post: async (url: string, body?: unknown) =>
    handleResponse(await fetch(url, jsonInit('POST', body))),
  put: async (url: string, body?: unknown) =>
    handleResponse(await fetch(url, jsonInit('PUT', body))),
  patch: async (url: string, body?: unknown) =>
    handleResponse(await fetch(url, jsonInit('PATCH', body))),
  del: async (url: string) =>
    handleResponse(await fetch(url, { method: 'DELETE', credentials: 'include' })),
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

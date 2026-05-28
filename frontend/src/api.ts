// ─── API helpers ─────────────────────────────────────
// Centralized fetch wrapper. On 401 we fire a single window-level
// `auth:expired` event so any view/button that hits an expired
// session redirects to the login screen — no per-caller handling
// needed (App.tsx listens once and clears auth state).
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

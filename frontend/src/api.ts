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
    throw new Error(err.detail || `Request failed (${r.status})`)
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

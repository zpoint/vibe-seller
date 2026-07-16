/**
 * Handlers for loading a Ziniao account's browser profiles (and the
 * force-restart retry).
 *
 * Extracted from App.tsx so the fetch → error-decoding sequence can be
 * reasoned about (and unit-tested) on its own, and to keep the app shell
 * under the module line limit. The error encoding is a colon-delimited
 * string the Sidebar UI decodes:
 *   `ziniao:<status>[:<base64 message>]`  — structured backend status
 *   `no_profiles` | `connect_error` | `api_error:<msg>` | `restart_failed:<msg>`
 */
import type { ZiniaoBrowserProfile } from '../types'

export interface BrowserProfilesApi {
  get(url: string): Promise<ZiniaoBrowserProfile[]>
  post(url: string, body?: unknown): Promise<unknown>
}

export interface BrowserProfilesDeps {
  api: BrowserProfilesApi
  /** Current error string — a `ziniao:running_normal[...]` value means the
   *  next fetch is a retry from the "browser already running" state. */
  browserFetchError: string
  setFetchingBrowsers: React.Dispatch<React.SetStateAction<boolean>>
  setZiniaoBrowsers: React.Dispatch<React.SetStateAction<ZiniaoBrowserProfile[]>>
  setSelectedBrowserOauth: React.Dispatch<React.SetStateAction<string>>
  setBrowserFetchError: React.Dispatch<React.SetStateAction<string>>
  setZiniaoRetried: React.Dispatch<React.SetStateAction<boolean>>
}

export async function fetchBrowserProfiles(
  accountId: string,
  deps: BrowserProfilesDeps,
): Promise<void> {
  if (!accountId) return
  const {
    api, browserFetchError, setFetchingBrowsers, setZiniaoBrowsers,
    setSelectedBrowserOauth, setBrowserFetchError, setZiniaoRetried,
  } = deps
  // Retry if previously in running_normal. The encoding is
  // `ziniao:running_normal` or `ziniao:running_normal:<base64msg>`.
  const wasNormalMode =
    browserFetchError === 'ziniao:running_normal' ||
    browserFetchError.startsWith('ziniao:running_normal:')
  setFetchingBrowsers(true); setZiniaoBrowsers([]); setSelectedBrowserOauth(''); setBrowserFetchError('')
  // Reset retry state unless this is a retry from running_normal
  if (!wasNormalMode) setZiniaoRetried(false)
  try {
    const browsers = await api.get(`/api/ziniao-accounts/${accountId}/browsers`)
    setZiniaoBrowsers(browsers)
    setZiniaoRetried(false)
    if (browsers.length === 0) setBrowserFetchError('no_profiles')
  } catch (e) {
    const msg = e instanceof Error ? e.message : ''
    // Try to parse structured JSON status from backend. The server
    // platform comes from /api/system/info (serverPlatform state) —
    // we only carry the ziniao status + its own error text here.
    try {
      const status = JSON.parse(msg)
      if (status.status) {
        if (wasNormalMode && status.status === 'running_normal') setZiniaoRetried(true)
        // Carry Ziniao's own err text through as an extra colon-
        // delimited field so the UI can surface it. base64 the
        // message so embedded colons / unicode don't tangle the
        // parser on the other side.
        const ziniaoMsg = (status.message || '').toString()
        const encoded = ziniaoMsg
          ? `:${btoa(unescape(encodeURIComponent(ziniaoMsg)))}`
          : ''
        setBrowserFetchError(`ziniao:${status.status}${encoded}`)
        setFetchingBrowsers(false)
        return
      }
    } catch { /* not JSON, fall through */ }
    // Fallback: existing string-matching logic
    if (msg.includes('/api/ziniao/launcher') || msg.includes('not running')) setBrowserFetchError('connect_error')
    else if (msg.includes('Ziniao API error') || msg.includes('-10003')) setBrowserFetchError('api_error:' + msg)
    else setBrowserFetchError('connect_error')
  }
  setFetchingBrowsers(false)
}

export async function restartZiniao(
  accountId: string,
  deps: BrowserProfilesDeps,
): Promise<void> {
  const { api, setFetchingBrowsers, setBrowserFetchError, setZiniaoRetried } = deps
  setFetchingBrowsers(true); setBrowserFetchError('')
  try {
    await api.post(`/api/ziniao-accounts/${accountId}/restart`)
    setZiniaoRetried(false)
    await fetchBrowserProfiles(accountId, deps)
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Restart failed'
    setBrowserFetchError('restart_failed:' + msg)
  }
  setFetchingBrowsers(false)
}

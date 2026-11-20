/**
 * Loading a Ziniao account's browser profiles for store setup.
 *
 * The server brings Ziniao up first: when it is not answering (opened by
 * hand in normal mode, not running, left over) it is closed and relaunched
 * in WebDriver mode — nobody is asked. That can take a couple of minutes,
 * so one request answers 503 `{"status":"starting","elapsed_s":N}` after
 * ~45 s and this handler asks again, keeping the spinner up and showing how
 * long it has been, until the list or a real failure comes back.
 *
 * Extracted from App.tsx so the fetch → error-decoding sequence can be
 * reasoned about (and unit-tested) on its own. The error encoding is a
 * colon-delimited string the store picker decodes:
 *   `ziniao:<status>[:<base64 message>]`  — structured backend status
 *   `no_profiles` | `connect_error` | `api_error:<msg>`
 */
import { LONG_TIMEOUT_MS, type RequestOptions } from '../lib/requestDeadline'
import type { ZiniaoBrowserProfile } from '../types'

export interface BrowserProfilesApi {
  get(url: string, opts?: RequestOptions): Promise<ZiniaoBrowserProfile[]>
}

export interface BrowserProfilesDeps {
  api: BrowserProfilesApi
  setFetchingBrowsers: React.Dispatch<React.SetStateAction<boolean>>
  setZiniaoBrowsers: React.Dispatch<React.SetStateAction<ZiniaoBrowserProfile[]>>
  setSelectedBrowserOauth: React.Dispatch<React.SetStateAction<string>>
  setBrowserFetchError: React.Dispatch<React.SetStateAction<string>>
  /** Seconds Ziniao has been starting; 0 when it is not (yet) starting. */
  setZiniaoStartingSecs: React.Dispatch<React.SetStateAction<number>>
  /** Pause between "still starting" answers (injected by tests). */
  sleep?: (ms: number) => Promise<void>
}

/** Ask again this soon after a "starting" answer. */
export const STARTING_POLL_MS = 1000
/** Stop asking after this long; a server round never takes this long. */
export const STARTING_GIVE_UP_MS = 8 * 60_000

// Only the latest fetch may update state: picking another account while
// Ziniao starts must not be overwritten by the old account's answer.
let latestFetch = 0

interface ZiniaoStatus { status: string; message?: string; elapsed_s?: number }

function parseStatus(msg: string): ZiniaoStatus | null {
  try {
    const parsed = JSON.parse(msg)
    return parsed && typeof parsed.status === 'string' ? parsed : null
  } catch { return null }
}

function encodeError(status: ZiniaoStatus | null, msg: string): string {
  if (status) {
    // Carry the server's own text as an extra colon-delimited field,
    // base64'd so embedded colons / unicode don't tangle the parser.
    const text = (status.message || '').toString()
    const encoded = text ? `:${btoa(unescape(encodeURIComponent(text)))}` : ''
    return `ziniao:${status.status}${encoded}`
  }
  if (msg.includes('/api/ziniao/launcher') || msg.includes('not running')) return 'connect_error'
  if (msg.includes('Ziniao API error') || msg.includes('-10003')) return 'api_error:' + msg
  return 'connect_error'
}

export async function fetchBrowserProfiles(
  accountId: string,
  deps: BrowserProfilesDeps,
): Promise<void> {
  if (!accountId) return
  const seq = ++latestFetch
  const {
    api, setFetchingBrowsers, setZiniaoBrowsers, setSelectedBrowserOauth,
    setBrowserFetchError, setZiniaoStartingSecs,
  } = deps
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>(r => setTimeout(r, ms)))
  const current = () => seq === latestFetch
  setFetchingBrowsers(true); setZiniaoBrowsers([]); setSelectedBrowserOauth('')
  setBrowserFetchError(''); setZiniaoStartingSecs(0)
  const began = Date.now()
  try {
    for (;;) {
      try {
        const browsers = await api.get(`/api/ziniao-accounts/${accountId}/browsers`, { timeoutMs: LONG_TIMEOUT_MS })
        if (!current()) return
        setZiniaoBrowsers(browsers)
        if (browsers.length === 0) setBrowserFetchError('no_profiles')
        return
      } catch (e) {
        if (!current()) return
        const msg = e instanceof Error ? e.message : ''
        const status = parseStatus(msg)
        if (status?.status === 'starting' && Date.now() - began < STARTING_GIVE_UP_MS) {
          setZiniaoStartingSecs(status.elapsed_s ?? Math.round((Date.now() - began) / 1000))
          await sleep(STARTING_POLL_MS)
          if (!current()) return
          continue
        }
        setBrowserFetchError(encodeError(status, msg))
        return
      }
    }
  } finally {
    if (current()) { setFetchingBrowsers(false); setZiniaoStartingSecs(0) }
  }
}

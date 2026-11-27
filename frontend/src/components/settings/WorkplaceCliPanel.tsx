import { useCallback, useEffect, useRef, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import { LONG_TIMEOUT_MS } from '../../lib/requestDeadline'
import { CLI_ERROR_HELP, CLI_NOTICE_HELP, DOCS_HELP } from '../../lib/docsHelp'
import { DocsHelpLink } from '../ui/DocsHelpLink'
import type { CliCard, LoginView, Notice } from './cliTypes'
import { useCliExpiry } from '../../hooks/useCliExpiry'
import { useScrollIntoViewOnHash } from '../../hooks/useScrollIntoViewOnHash'
import { INTEGRATION_ATTENTION_EVENT } from '../../hooks/useIntegrationAttention'

/** Settings → Integrations: the official WeCom / Feishu / DingTalk CLIs,
 *  and Google Workspace's (its own card, same rows).
 *  (Shown to users as "WeCom / Feishu / DingTalk" — the
 *  page never says CLI; see integrations.cli in the locales.)
 *
 *  One row per platform. "Connect" downloads the CLI, runs its login
 *  (QR or link — no terminal) and, once signed in, enables it: the
 *  agent then gets the platform's official skills and the CLI on its
 *  PATH. Each platform is independent — agents see exactly the enabled
 *  ones. The backend owns the flow (`enable_after` finishes it even if
 *  this page is closed mid-login); the page only polls. */

/** Where to read how to act on a notice: the vendor's own guide, or
 *  ours. The link reads ``guideLink.<key>`` (default ``officialGuide``). */
const NOTICE_GUIDES: Record<string, { zh: string; en: string }> = {
  // DingTalk's own permission guide (Chinese only): where 「CLI设置」
  // is and which channel Vibe Seller falls under.
  dingtalk_cli_access: {
    zh: 'https://open.dingtalk.com/document/development/dws-permission-management-guide',
    en: 'https://open.dingtalk.com/document/development/dws-permission-management-guide',
  },
  // What the withheld permissions do and how the Feishu app's admin
  // grants one (FAQ in docs/workplace-cli-feishu.md).
  feishu_missing_scopes: DOCS_HELP.feishuMissingScopes,
}

/** Feishu permissions an org admin often withholds, named by the
 *  feature they gate (``scopeFeature.<feature>``) so the page says what
 *  stops working instead of only a permission code.
 *  vc:meeting.realtime:read gates only ``lark-cli vc +meeting-screenshot``,
 *  which lark-cli hid and took out of its skills (larksuite/cli#2909),
 *  yet ``auth login --recommend`` still asks for it. */
const FEISHU_SCOPE_FEATURES: Record<string, string> = {
  'vc:meeting.realtime:read': 'meetingScreenshot',
}

/** The withheld scopes a ``feishu_missing_scopes`` notice carries. */
function withheldScopes(n: Notice): string[] {
  return (n.detail ?? '').split(/\s+/).filter(Boolean)
}

/** Where each step of Google's one-time setup is done (Google Cloud
 *  console). The steps' text is ``integrations.cli.setup.google_client.steps``. */
const GOOGLE_APIS = [
  'gmail.googleapis.com', 'drive.googleapis.com', 'sheets.googleapis.com',
  'docs.googleapis.com', 'calendar-json.googleapis.com',
].join(',')
const SETUP_LINKS: Record<string, (string | null)[]> = {
  google_client: [
    'https://console.cloud.google.com/projectcreate',
    `https://console.cloud.google.com/flows/enableapi?apiid=${GOOGLE_APIS}`,
    'https://console.cloud.google.com/auth/overview',
    'https://console.cloud.google.com/auth/audience',
    'https://console.cloud.google.com/auth/clients',
    null,
  ],
}

const BASE = '/api/integrations/cli'
const POLL_MS = 2000

/** A failed request, as the page shows it: the server's ``code`` picks
 *  a sentence saying what to do next; the raw text is folded away. */
interface CliError {
  code: string
  message: string
}

function errView(e: unknown, fallback = 'unexpected'): CliError {
  const detail = (e as { detail?: unknown } | null)?.detail
  if (detail && typeof detail === 'object' && 'code' in detail) {
    const d = detail as { code: unknown; message?: unknown }
    return { code: String(d.code), message: String(d.message ?? '') }
  }
  return { code: fallback, message: e instanceof Error ? e.message : String(e) }
}

const ERROR_CODES = new Set([
  'not_installed', 'not_signed_in', 'in_use', 'does_not_run',
  'unsupported', 'download_failed', 'unexpected', 'load',
  'needs_setup', 'google_client_web', 'google_client_invalid',
  'bad_address', 'no_login',
])

/** The sentence for *err*, plus its technical text under "Details". */
function ErrorLine({ err, name }: { err: CliError; name: string }) {
  const { t } = useTranslation()
  const code = ERROR_CODES.has(err.code) ? err.code : 'unexpected'
  const help = CLI_ERROR_HELP[code]
  return (
    <div className="text-sm text-red-600 break-words" data-testid="cli-error">
      <p>{t(`integrations.cli.error.${code}`, { name })}{help && <> <DocsHelpLink topic={help} /></>}</p>
      {err.message && <Details text={err.message} />}
    </div>
  )
}

function Details({ text }: { text: string }) {
  const { t } = useTranslation()
  return (
    <details className="text-xs text-gray-500">
      <summary className="cursor-pointer">{t('integrations.cli.details')}</summary>
      <p className="mt-1 font-mono break-all">{text}</p>
    </details>
  )
}

/** The platforms of one card: the workplace ones, or Google Workspace. */
const GOOGLE_KEYS = ['gws']

export function WorkplaceCliPanel({
  isAdmin = false,
  google = false,
}: {
  isAdmin?: boolean
  /** The Google Workspace card instead of the WeCom / Feishu / DingTalk one. */
  google?: boolean
}) {
  const { t } = useTranslation()
  const [cards, setCards] = useState<CliCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<CliError | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = (await api.get(BASE)) as { integrations?: CliCard[] }
      const all = Array.isArray(r?.integrations) ? r.integrations : []
      setCards(all.filter((c) => GOOGLE_KEYS.includes(c.key) === google))
      setError(null)
      window.dispatchEvent(new Event(INTEGRATION_ATTENTION_EVENT))
    } catch (e) {
      setError(errView(e, 'load'))
    } finally {
      setLoading(false)
    }
  }, [google])

  useEffect(() => { refresh() }, [refresh])

  const group = google ? 'integrations.gws' : 'integrations.cli'
  return (
    <div
      className="bg-white rounded-lg border border-gray-200 p-4"
      data-testid={google ? 'gws-card' : 'cli-card'}
    >
      <h3 className="font-semibold mb-1">{t(`${group}.title`)}</h3>
      <p className="text-xs text-gray-500 mb-3">{t(`${group}.description`)}</p>
      {loading ? (
        <p className="text-sm text-gray-500">{t('common.loading')}</p>
      ) : (
        <div className="space-y-3">
          {cards.map((c) => (
            <CliRow key={c.key} card={c} isAdmin={isAdmin} onChanged={refresh} />
          ))}
          {error && <ErrorLine err={{ ...error, code: 'load' }} name="" />}
        </div>
      )}
    </div>
  )
}

function CliRow({
  card,
  isAdmin,
  onChanged,
}: {
  card: CliCard
  isAdmin: boolean
  onChanged: () => Promise<void>
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<CliError | null>(null)
  const [login, setLogin] = useState<LoginView | null>(null)
  const [qrTick, setQrTick] = useState(0)
  // The one-time setup form is open (Connect found it missing, or the
  // admin chose to replace what was set up).
  const [setupOpen, setSetupOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Bumped by every start / cancel: a poll answered after that belongs
  // to a login the person already moved on from, and must not reopen
  // the panel they just closed.
  const generation = useRef(0)

  const stopPolling = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
  }
  useEffect(() => stopPolling, [])

  const poll = useCallback(async () => {
    const mine = generation.current
    try {
      const v = (await api.get(`${BASE}/${card.key}/login`)) as LoginView
      if (mine !== generation.current) return
      setLogin(v)
      if (v.qr) setQrTick((n) => n + 1)
      if (inProgress(v)) {
        timer.current = setTimeout(poll, POLL_MS)
        return
      }
      await onChanged()
    } catch (e) {
      setError(errView(e))
    }
  }, [card.key, onChanged])

  // A login an admin started earlier (another tab, before a reload) is
  // picked up again so its QR / link is not lost.
  useEffect(() => {
    if (!isAdmin) return
    if (card.login && inProgress(card.login) && !timer.current) {
      timer.current = setTimeout(poll, 0)
    }
  }, [card.login, isAdmin, poll])

  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(errView(e))
    } finally {
      setBusy(null)
    }
  }

  const startLogin = async () => {
    stopPolling()
    generation.current += 1
    const v = (await api.post(`${BASE}/${card.key}/login`, { enable_after: true })) as LoginView
    setLogin(v)
    timer.current = setTimeout(poll, 500)
  }

  const connect = () =>
    run('connect', async () => {
      if (!card.installed) {
        await api.post(`${BASE}/${card.key}/install`, {}, { timeoutMs: LONG_TIMEOUT_MS })
      }
      if (card.installed && card.auth?.usable) {
        await api.post(`${BASE}/${card.key}/enable`, {}, { timeoutMs: LONG_TIMEOUT_MS })
        await onChanged()
        return
      }
      await onChanged()
      // A platform with a one-time setup (Google's sign-in key): the
      // sign-in can only start once it is done — ask the fresh row.
      if (card.setup && (await freshCard(card.key))?.needs_setup) {
        setSetupOpen(true)
        return
      }
      await startLogin()
    })

  const submitSetup = (payload: string) =>
    run('setup', async () => {
      await api.post(`${BASE}/${card.key}/setup`, { payload })
      setSetupOpen(false)
      await onChanged()
      await startLogin()
    })

  const pasteBack = (address: string) =>
    run('paste', async () => {
      await api.post(`${BASE}/${card.key}/login/paste`, { address })
    })

  const relogin = () => run('login', startLogin)

  const disable = () =>
    run('disable', async () => {
      await api.post(`${BASE}/${card.key}/disable`, {}, { timeoutMs: LONG_TIMEOUT_MS })
      await onChanged()
    })

  const cancel = () =>
    run('cancel', async () => {
      stopPolling()
      generation.current += 1
      await api.del(`${BASE}/${card.key}/login`)
      setLogin(null)
      await onChanged()
    })

  const loginActive = login && inProgress(login)
  const signedIn = !!card.auth?.usable
  const expiry = useCliExpiry(card.key, !!card.checks_expiry && card.enabled && signedIn, card)
  useScrollIntoViewOnHash(`cli-${card.key}`)
  // Signed in before (enabled), now the sign-in or a capability expired.
  const reauth = card.enabled && !!card.auth && (!signedIn || expiry.expired === true)
  const name = t(`integrations.cli.platform.${card.key}`, { defaultValue: card.title })
  // The button on screen once a sign-in ended: "Sign in again" on an
  // enabled platform, otherwise "Connect" (the only one a stopped or
  // never-enabled row shows).
  const retryButton = t(card.enabled ? 'integrations.cli.relogin' : 'integrations.cli.connect')

  return (
    <div id={`cli-${card.key}`} className="p-3 bg-gray-50 rounded-lg space-y-2" data-testid={`cli-row-${card.key}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">{name}</p>
          <p className="text-xs text-gray-500" data-testid={`cli-status-${card.key}`}>
            {card.installed
              ? t('integrations.cli.version', { version: card.version ?? '' })
              : t('integrations.cli.notInstalled')}
            {card.installed && (
              <>
                {' · '}
                {signedIn
                  ? t('integrations.cli.signedInAs', { identity: card.auth?.identity || '✓' })
                  : card.auth?.reason === 'expired'
                    ? t('integrations.cli.signInExpired')
                    : t('integrations.cli.notSignedIn')}
              </>
            )}
            {' · '}
            {card.enabled && card.installed && card.auth && !signedIn ? (
              // Enabled, but the sign-in stopped working (expired,
              // revoked): the AI's calls fail until someone signs in.
              <span className="text-amber-700" data-testid={`cli-needs-signin-${card.key}`}>
                {t('integrations.cli.enabledNeedsSignIn')}
                {card.key === 'gws' && <> <DocsHelpLink topic="googleSignInExpired" /></>}
              </span>
            ) : expiry.expired === true ? (
              <span className="text-amber-700" data-testid={`cli-expired-${card.key}`}>
                {t('integrations.cli.expired')}
              </span>
            ) : (
              <span className={card.enabled ? 'text-green-700' : ''}>
                {card.enabled ? t('integrations.cli.enabled') : t('integrations.cli.disabled')}
              </span>
            )}
          </p>
        </div>
        {isAdmin && (
          <div className="flex gap-2 shrink-0">
            {card.setup && card.installed && !card.needs_setup && !loginActive && !setupOpen && (
              <button
                onClick={() => setSetupOpen(true)}
                disabled={!!busy}
                data-testid={`cli-setup-open-${card.key}`}
                className="px-3 py-1.5 text-sm rounded bg-gray-200 text-gray-800 hover:bg-gray-300 disabled:opacity-40"
              >
                {t(`integrations.cli.setup.${card.setup}.replace`)}
              </button>
            )}
            {!card.enabled && !loginActive && !setupOpen && (
              <button
                onClick={connect}
                disabled={!!busy}
                data-testid={`cli-connect-${card.key}`}
                className="px-3 py-1.5 text-sm rounded font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
              >
                {busy === 'connect' ? t('integrations.cli.working') : t('integrations.cli.connect')}
              </button>
            )}
            {card.enabled && !loginActive && (
              <button
                onClick={relogin}
                disabled={!!busy}
                data-testid={`cli-relogin-${card.key}`}
                className={`px-3 py-1.5 text-sm rounded disabled:opacity-40 ${reauth ? 'font-medium bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-gray-200 text-gray-800 hover:bg-gray-300'}`}
              >
                {t(reauth ? 'integrations.cli.reauthorize' : 'integrations.cli.relogin')}
              </button>
            )}
            {card.enabled && (
              <button
                onClick={disable}
                disabled={!!busy}
                data-testid={`cli-disable-${card.key}`}
                className="px-3 py-1.5 text-sm rounded bg-gray-200 text-gray-800 hover:bg-gray-300 disabled:opacity-40"
              >
                {t('integrations.cli.disable')}
              </button>
            )}
          </div>
        )}
      </div>

      {expiry.expired === true && (
        <p className="flex flex-wrap items-center gap-x-1 text-xs text-amber-800" data-testid={`cli-expiry-hint-${card.key}`}>
          {t('integrations.cli.expiredHint')}
          <DocsHelpLink topic="wecomPermissionExpired" />
        </p>
      )}
      {expiry.loading && (
        <p className="text-xs text-gray-500" data-testid={`cli-expiry-checking-${card.key}`}>
          {t('integrations.cli.expiryChecking')}
        </p>
      )}

      {card.notice && !card.enabled && (
        <p className="text-sm text-amber-700" data-testid={`cli-row-notice-${card.key}`}>
          {t(`integrations.cli.rowNotice.${card.notice}`)}
        </p>
      )}

      {setupOpen && card.setup && !loginActive && (
        <SetupForm
          kind={card.setup}
          cardKey={card.key}
          busy={busy === 'setup'}
          onSubmit={submitSetup}
          onCancel={() => { setSetupOpen(false); setError(null) }}
        />
      )}

      {loginActive && (
        <div className="p-3 bg-white rounded border border-indigo-200 space-y-2" data-testid={`cli-login-${card.key}`}>
          {login.finishing ? (
            <p className="text-sm" data-testid={`cli-finishing-${card.key}`}>
              {t('integrations.cli.finishing')}
            </p>
          ) : (
          <>
          <p className="text-sm">
            {login.steps > 1 && (
              <span className="text-gray-500 mr-1">
                {t('integrations.cli.step', { step: login.step, steps: login.steps })}
              </span>
            )}
            {t(`integrations.cli.hint.${login.hint || 'open_url'}`)}
          </p>
          {login.state === 'starting' && (
            <p className="text-xs text-gray-500">{t('integrations.cli.preparing')}</p>
          )}
          {login.qr && (
            <img
              src={`${BASE}/${card.key}/login/qr?t=${qrTick}`}
              alt={t('integrations.cli.qrAlt')}
              className="w-48 h-48 border rounded"
              data-testid={`cli-login-qr-${card.key}`}
            />
          )}
          {login.url && (
            <a
              href={login.url}
              target="_blank"
              rel="noreferrer"
              className="block text-sm text-indigo-600 hover:underline break-all"
              data-testid={`cli-login-url-${card.key}`}
            >
              {shortLink(login.url)}
            </a>
          )}
          {login.paste_back && login.callback && (
            <PasteBack
              cardKey={card.key}
              callback={login.callback}
              busy={busy === 'paste'}
              onSubmit={pasteBack}
            />
          )}
          {login.user_code && (
            <p className="text-sm">
              {t('integrations.cli.userCode')}{' '}
              <span className="font-mono font-semibold" data-testid={`cli-login-code-${card.key}`}>
                {login.user_code}
              </span>
            </p>
          )}
          <button
            onClick={cancel}
            className="text-xs text-gray-600 hover:underline"
            data-testid={`cli-cancel-${card.key}`}
          >
            {t('integrations.cli.cancel')}
          </button>
          </>
          )}
        </div>
      )}

      {busy === 'connect' && !card.installed && (
        <p className="text-xs text-gray-500" data-testid={`cli-downloading-${card.key}`}>
          {t('integrations.cli.downloading')}
        </p>
      )}
      {login && !loginActive && (
        <LoginOutcome login={login} cardKey={card.key} retryButton={retryButton} />
      )}
      {error && <ErrorLine err={error} name={name} />}
    </div>
  )
}

/** A link as the page prints it: whole when short; a long one (Google's
 *  sign-in link runs to hundreds of characters) as its site and "…". */
function shortLink(url: string): string {
  if (url.length <= 120) return url
  try {
    return `${new URL(url).origin}/…`
  } catch {
    return `${url.slice(0, 117)}…`
  }
}

/** The row as the server has it now (after an install, before the
 *  parent's refresh has reached this row's props). */
async function freshCard(key: string): Promise<CliCard | undefined> {
  const r = (await api.get(BASE)) as { integrations?: CliCard[] }
  return (r?.integrations ?? []).find((c) => c.key === key)
}

/** The one-time setup of *kind*: what to do where, then the file the
 *  person made (Google: the downloaded sign-in key). */
function SetupForm({
  kind,
  cardKey,
  busy,
  onSubmit,
  onCancel,
}: {
  kind: string
  cardKey: string
  busy: boolean
  onSubmit: (payload: string) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [fileError, setFileError] = useState(false)
  const steps = t(`integrations.cli.setup.${kind}.steps`, { returnObjects: true }) as string[]
  const links = SETUP_LINKS[kind] ?? []
  const pick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setFileError(false)
    try {
      onSubmit(await readText(file))
    } catch {
      setFileError(true)
    }
  }
  return (
    <div className="p-3 bg-white rounded border border-indigo-200 space-y-2" data-testid={`cli-setup-${cardKey}`}>
      <p className="text-sm font-medium">{t(`integrations.cli.setup.${kind}.title`)}</p>
      <p className="text-xs text-gray-600">{t(`integrations.cli.setup.${kind}.intro`)}</p>
      <ol className="list-decimal pl-5 space-y-1 text-sm" data-testid={`cli-setup-steps-${cardKey}`}>
        {Array.isArray(steps) && steps.map((s, i) => (
          <li key={i}>
            {s}
            {links[i] && (
              <>
                {' '}
                <a href={links[i]!} target="_blank" rel="noreferrer"
                  className="text-indigo-600 hover:underline whitespace-nowrap">
                  {t('integrations.cli.setup.open')}
                </a>
              </>
            )}
          </li>
        ))}
      </ol>
      <div className="flex items-center gap-3">
        <label className={`px-3 py-1.5 text-sm rounded font-medium bg-indigo-600 text-white hover:bg-indigo-700 cursor-pointer ${busy ? 'opacity-40 pointer-events-none' : ''}`}>
          {busy ? t('integrations.cli.working') : t(`integrations.cli.setup.${kind}.choose`)}
          <input
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={pick}
            data-testid={`cli-setup-file-${cardKey}`}
          />
        </label>
        <button onClick={onCancel} className="text-xs text-gray-600 hover:underline"
          data-testid={`cli-setup-cancel-${cardKey}`}>
          {t('integrations.cli.cancel')}
        </button>
      </div>
      {fileError && <p className="text-sm text-red-600">{t('integrations.cli.setup.unreadable')}</p>}
    </div>
  )
}

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file)
  })
}

/** For a browser that is not on the server's computer: the approval
 *  lands on an address only that computer can open. The person pastes
 *  it here and the server hands it on. */
function PasteBack({
  cardKey,
  callback,
  busy,
  onSubmit,
}: {
  cardKey: string
  callback: string
  busy: boolean
  onSubmit: (address: string) => void
}) {
  const { t } = useTranslation()
  const [address, setAddress] = useState('')
  return (
    <div className="space-y-1 pt-1 border-t border-gray-100" data-testid={`cli-paste-${cardKey}`}>
      <p className="text-xs text-gray-600">{t('integrations.cli.pasteBack.hint', { address: callback })}</p>
      <div className="flex gap-2">
        <input
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder={`${callback}/?…`}
          className="flex-1 min-w-0 px-2 py-1 text-sm border rounded font-mono"
          data-testid={`cli-paste-input-${cardKey}`}
        />
        <button
          onClick={() => onSubmit(address)}
          disabled={busy || !address.trim()}
          className="px-3 py-1 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
          data-testid={`cli-paste-submit-${cardKey}`}
        >
          {t('integrations.cli.pasteBack.submit')}
        </button>
      </div>
    </div>
  )
}

/** Still running: the CLI's sign-in, or what follows it once signed in
 *  (enabling the platform). Its outcome is not final until this is
 *  false. */
function inProgress(v: Pick<Partial<LoginView>, 'state' | 'finishing'>): boolean {
  return v.state === 'starting' || v.state === 'waiting' || !!v.finishing
}

/** How a finished sign-in ended, when there is something to say: a
 *  failure, or a success with a notice (e.g. scopes the org withheld). */
function LoginOutcome({
  login,
  cardKey,
  retryButton,
}: {
  login: LoginView
  cardKey: string
  retryButton: string
}) {
  const { t } = useTranslation()
  const notices = login.notices ?? []
  if (login.state !== 'failed' && login.state !== 'succeeded') return null
  if (login.state === 'succeeded' && notices.length === 0 && !login.error) return null
  const failed = login.state === 'failed' || !!login.error
  // Enabling is what failed after a good sign-in: "Connect" retries it.
  const button = login.error === 'enable_failed' ? t('integrations.cli.connect') : retryButton
  return (
    <div
      className={`text-sm space-y-2 break-words ${failed ? 'text-red-600' : 'text-amber-700'}`}
      data-testid={`cli-outcome-${cardKey}`}
    >
      {failed && notices.length === 0 && (
        <p>
          {login.error
            ? t(`integrations.cli.loginError.${login.error}`, { button })
            : t('integrations.cli.loginFailed', { button })}
          {login.error === 'denied' && <> <DocsHelpLink topic="googleAccessBlocked" /></>}
        </p>
      )}
      {notices.map((n) => (
        <NoticeBlock key={n.key} notice={n} cardKey={cardKey} button={button} />
      ))}
      {failed && login.message && notices.length === 0 && <Details text={login.message} />}
    </div>
  )
}

/** A notice's detail as the page shows it: an expiry is a moment, said
 *  in the reader's own time zone and language; a withheld permission is
 *  the feature it gates, with the code an admin searches the console for. */
function noticeDetail(n: Notice, lang: string, t: TFunction): string {
  if (!n.detail) return ''
  if (n.key === 'feishu_no_refresh_token') {
    const at = new Date(n.detail)
    if (!Number.isNaN(at.getTime())) return at.toLocaleString(lang)
  }
  if (n.key === 'feishu_missing_scopes') {
    return withheldScopes(n)
      .map((scope) => {
        const feature = FEISHU_SCOPE_FEATURES[scope]
        return feature
          ? t('integrations.cli.scopeWithCode', {
              feature: t(`integrations.cli.scopeFeature.${feature}`),
              scope,
            })
          : scope
      })
      .join(t('integrations.cli.listSeparator'))
  }
  return n.detail
}

function NoticeBlock({ notice, cardKey, button }: { notice: Notice; cardKey: string; button: string }) {
  const { t, i18n } = useTranslation()
  const guide = NOTICE_GUIDES[notice.key]
  const guideUrl = guide && (i18n.language?.startsWith('zh') ? guide.zh : guide.en)
  const help = CLI_NOTICE_HELP[notice.key]
  // What the person (often someone else: an admin) does, step by step.
  const stepsKey = `integrations.cli.noticeSteps.${notice.key}`
  const steps = i18n.exists(stepsKey)
    ? (t(stepsKey, { returnObjects: true, button }) as string[])
    : []
  return (
    <div className="space-y-1" data-testid={`cli-notice-${cardKey}-${notice.key}`}>
      <p>
        {t(`integrations.cli.notice.${notice.key}`, {
          detail: noticeDetail(notice, i18n.language, t),
          button,
        })}
      </p>
      {notice.key === 'dingtalk_cli_access' && notice.detail && (
        <p>{t('integrations.cli.orgAdmins', { names: notice.detail })}</p>
      )}
      {steps.length > 0 && (
        <ol className="list-decimal pl-5 space-y-0.5" data-testid={`cli-notice-steps-${cardKey}`}>
          {steps.map((s, i) => <li key={i}>{s}</li>)}
        </ol>
      )}
      {(notice.url || guideUrl || help) && (
        <p className="space-x-3">
          {notice.url && (
            <a href={notice.url} target="_blank" rel="noreferrer"
              className="text-indigo-600 hover:underline" data-testid={`cli-help-${cardKey}`}>
              {t(`integrations.cli.helpLink.${notice.key}`, {
                defaultValue: t('integrations.cli.helpLink.default'),
              })}
            </a>
          )}
          {guideUrl && (
            <a href={guideUrl} target="_blank" rel="noreferrer"
              className="text-indigo-600 hover:underline" data-testid={`cli-guide-${cardKey}`}>
              {t(`integrations.cli.guideLink.${notice.key}`, {
                defaultValue: t('integrations.cli.officialGuide'),
              })}
            </a>
          )}
          {help && <DocsHelpLink topic={help} />}
        </p>
      )}
    </div>
  )
}

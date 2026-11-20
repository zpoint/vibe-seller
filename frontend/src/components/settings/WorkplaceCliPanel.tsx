import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import { LONG_TIMEOUT_MS } from '../../lib/requestDeadline'

/** Settings → Integrations: the official WeCom / Feishu / DingTalk CLIs.
 *  (Shown to users as "Let the AI use WeCom / Feishu / DingTalk" — the
 *  page never says CLI; see integrations.cli in the locales.)
 *
 *  One row per platform. "Connect" downloads the CLI, runs its login
 *  (QR or link — no terminal) and, once signed in, enables it: the
 *  agent then gets the platform's official skills and the CLI on its
 *  PATH. Each platform is independent — agents see exactly the enabled
 *  ones. The backend owns the flow (`enable_after` finishes it even if
 *  this page is closed mid-login); the page only polls. */

interface LoginView {
  state: 'starting' | 'waiting' | 'succeeded' | 'failed' | 'cancelled'
  url: string | null
  user_code: string | null
  qr: boolean
  message: string | null
  step: number
  steps: number
  hint: string | null
  /** What the person must know about the outcome — several can apply
   *  (scopes withheld AND a sign-in that cannot renew). Set by the
   *  server's parsers: an i18n key, its detail and a link to act on. */
  notices?: Notice[]
  /** Why the sign-in ended badly when no notice explains it: the
   *  runner's ``timeout`` or ``enable_failed`` (i18n key). */
  error?: string | null
}

interface Notice {
  key: string
  detail: string | null
  url: string | null
}

/** The vendor's own guide for each notice the server can raise. */
const NOTICE_GUIDES: Record<string, { zh: string; en: string }> = {
  dingtalk_cli_access: {
    zh: 'https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/blob/main/README_zh.md#开始使用',
    en: 'https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli#getting-started',
  },
}

interface CliCard {
  key: string
  title: string
  binary: string
  homepage: string
  installed: boolean
  version: string | null
  enabled: boolean
  auth: { usable: boolean; reason: string; identity: string | null } | null
  login: Partial<LoginView> | null
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
])

/** The sentence for *err*, plus its technical text under "Details". */
function ErrorLine({ err, name }: { err: CliError; name: string }) {
  const { t } = useTranslation()
  const code = ERROR_CODES.has(err.code) ? err.code : 'unexpected'
  return (
    <div className="text-sm text-red-600 break-words" data-testid="cli-error">
      <p>{t(`integrations.cli.error.${code}`, { name })}</p>
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

export function WorkplaceCliPanel({ isAdmin = false }: { isAdmin?: boolean }) {
  const { t } = useTranslation()
  const [cards, setCards] = useState<CliCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<CliError | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = (await api.get(BASE)) as { integrations?: CliCard[] }
      setCards(Array.isArray(r?.integrations) ? r.integrations : [])
      setError(null)
    } catch (e) {
      setError(errView(e, 'load'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4" data-testid="cli-card">
      <h3 className="font-semibold mb-1">{t('integrations.cli.title')}</h3>
      <p className="text-xs text-gray-500 mb-3">{t('integrations.cli.description')}</p>
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
      if (v.state === 'starting' || v.state === 'waiting') {
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
    const s = card.login?.state
    if ((s === 'starting' || s === 'waiting') && !timer.current) {
      timer.current = setTimeout(poll, 0)
    }
  }, [card.login?.state, isAdmin, poll])

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
      await startLogin()
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

  const loginActive = login && (login.state === 'starting' || login.state === 'waiting')
  const signedIn = !!card.auth?.usable
  const name = t(`integrations.cli.platform.${card.key}`, { defaultValue: card.title })
  // The button on screen once a sign-in ended: "Sign in again" for a
  // platform that is signed in or enabled, otherwise "Connect".
  const retryButton = t(
    card.installed && (signedIn || card.enabled)
      ? 'integrations.cli.relogin'
      : 'integrations.cli.connect',
  )

  return (
    <div className="p-3 bg-gray-50 rounded-lg space-y-2" data-testid={`cli-row-${card.key}`}>
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
                  : t('integrations.cli.notSignedIn')}
              </>
            )}
            {' · '}
            <span className={card.enabled ? 'text-green-700' : ''}>
              {card.enabled ? t('integrations.cli.enabled') : t('integrations.cli.disabled')}
            </span>
          </p>
        </div>
        {isAdmin && (
          <div className="flex gap-2 shrink-0">
            {!card.enabled && !loginActive && (
              <button
                onClick={connect}
                disabled={!!busy}
                data-testid={`cli-connect-${card.key}`}
                className="px-3 py-1.5 text-sm rounded font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
              >
                {busy === 'connect' ? t('integrations.cli.working') : t('integrations.cli.connect')}
              </button>
            )}
            {card.installed && !loginActive && (signedIn || card.enabled) && (
              <button
                onClick={relogin}
                disabled={!!busy}
                data-testid={`cli-relogin-${card.key}`}
                className="px-3 py-1.5 text-sm rounded bg-gray-200 text-gray-800 hover:bg-gray-300 disabled:opacity-40"
              >
                {t('integrations.cli.relogin')}
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

      {loginActive && (
        <div className="p-3 bg-white rounded border border-indigo-200 space-y-2" data-testid={`cli-login-${card.key}`}>
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
              {login.url}
            </a>
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
 *  in the reader's own time zone and language. */
function noticeDetail(n: Notice, lang: string): string {
  if (!n.detail) return ''
  if (n.key === 'feishu_no_refresh_token') {
    const at = new Date(n.detail)
    if (!Number.isNaN(at.getTime())) return at.toLocaleString(lang)
  }
  return n.detail
}

function NoticeBlock({ notice, cardKey, button }: { notice: Notice; cardKey: string; button: string }) {
  const { t, i18n } = useTranslation()
  const guide = NOTICE_GUIDES[notice.key]
  const guideUrl = guide && (i18n.language?.startsWith('zh') ? guide.zh : guide.en)
  return (
    <div className="space-y-1" data-testid={`cli-notice-${cardKey}-${notice.key}`}>
      <p>
        {t(`integrations.cli.notice.${notice.key}`, {
          detail: noticeDetail(notice, i18n.language),
          button,
        })}
      </p>
      {notice.key === 'dingtalk_cli_access' && notice.detail && (
        <p>{t('integrations.cli.orgAdmins', { names: notice.detail })}</p>
      )}
      {(notice.url || guideUrl) && (
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
              {t('integrations.cli.officialGuide')}
            </a>
          )}
        </p>
      )}
    </div>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import { LONG_TIMEOUT_MS } from '../../lib/requestDeadline'

/** Settings → Integrations: the official WeCom / Feishu / DingTalk CLIs.
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
  /** What the person must know about the outcome (i18n key), its detail
   *  and a link to act on it — set by the server's parsers. */
  notice?: string | null
  notice_detail?: string | null
  help_url?: string | null
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

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export function WorkplaceCliPanel({ isAdmin = false }: { isAdmin?: boolean }) {
  const { t } = useTranslation()
  const [cards, setCards] = useState<CliCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = (await api.get(BASE)) as { integrations?: CliCard[] }
      setCards(Array.isArray(r?.integrations) ? r.integrations : [])
      setError(null)
    } catch (e) {
      setError(errText(e))
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
          {error && <p className="text-sm text-red-600">{error}</p>}
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
  const [error, setError] = useState<string | null>(null)
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
      setError(errText(e))
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
      setError(errText(e))
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

  const update = () =>
    run('update', async () => {
      await api.post(`${BASE}/${card.key}/install`, {}, { timeoutMs: LONG_TIMEOUT_MS })
      await onChanged()
    })

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

  return (
    <div className="p-3 bg-gray-50 rounded-lg space-y-2" data-testid={`cli-row-${card.key}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">{card.title}</p>
          <p className="text-xs text-gray-500" data-testid={`cli-status-${card.key}`}>
            {card.installed
              ? `${card.binary} ${card.version ?? ''}`
              : t('integrations.cli.notInstalled')}
            {' · '}
            {signedIn
              ? t('integrations.cli.signedInAs', { identity: card.auth?.identity || '✓' })
              : card.installed
              ? t('integrations.cli.notSignedIn')
              : ''}
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
            {card.installed && !loginActive && (
              <button
                onClick={update}
                disabled={!!busy}
                data-testid={`cli-update-${card.key}`}
                className="px-3 py-1.5 text-sm rounded bg-gray-200 text-gray-800 hover:bg-gray-300 disabled:opacity-40"
              >
                {busy === 'update' ? t('integrations.cli.working') : t('integrations.cli.update')}
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

      {login && !loginActive && <LoginOutcome login={login} cardKey={card.key} />}
      {error && <p className="text-sm text-red-600 break-words">{error}</p>}
    </div>
  )
}

/** How a finished sign-in ended, when there is something to say: a
 *  failure, or a success with a notice (e.g. scopes the org withheld). */
function LoginOutcome({ login, cardKey }: { login: LoginView; cardKey: string }) {
  const { t, i18n } = useTranslation()
  if (login.state !== 'failed' && login.state !== 'succeeded') return null
  if (login.state === 'succeeded' && !login.notice && !login.message) return null
  const failed = login.state === 'failed'
  const guide = login.notice ? NOTICE_GUIDES[login.notice] : undefined
  const guideUrl = guide && (i18n.language?.startsWith('zh') ? guide.zh : guide.en)
  return (
    <div
      className={`text-sm space-y-1 break-words ${failed ? 'text-red-600' : 'text-amber-700'}`}
      data-testid={`cli-outcome-${cardKey}`}
    >
      {failed && !login.notice && <p>{t('integrations.cli.loginFailed')}</p>}
      {login.notice && (
        <p>{t(`integrations.cli.notice.${login.notice}`, { detail: login.notice_detail ?? '' })}</p>
      )}
      {login.notice === 'dingtalk_cli_access' && login.notice_detail && (
        <p>{t('integrations.cli.orgAdmins', { names: login.notice_detail })}</p>
      )}
      {(login.help_url || guideUrl) && (
        <p className="space-x-3">
          {login.help_url && (
            <a href={login.help_url} target="_blank" rel="noreferrer"
              className="text-indigo-600 hover:underline" data-testid={`cli-help-${cardKey}`}>
              {t(`integrations.cli.helpLink.${login.notice ?? 'default'}`)}
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
      {login.message && !login.notice && (
        <p className="text-xs text-gray-600">{login.message}</p>
      )}
    </div>
  )
}

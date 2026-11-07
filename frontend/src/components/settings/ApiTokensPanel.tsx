import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiTokens } from '../../api'
import type { ApiToken } from '../../types'
import { ApiTokenCreatedModal } from './ApiTokenCreatedModal'

interface ApiTokensPanelProps {
  /** Whether the server requires a login. When it doesn't, a token is
   *  not needed to talk to it — but tokens still work, so the page
   *  says so instead of pretending they are required. */
  authRequired: boolean
}

/** Expiry choices, in days. `null` = never expires. The API's own
 *  default is 90 days when the field is omitted, so the form always
 *  sends it explicitly — and the form's default is "never". */
const EXPIRY_CHOICES: { days: number | null; labelKey: string }[] = [
  { days: 30, labelKey: 'apiTokens.expiry30' },
  { days: 90, labelKey: 'apiTokens.expiry90' },
  { days: 365, labelKey: 'apiTokens.expiry365' },
  { days: null, labelKey: 'apiTokens.expiryNever' },
]
const DEFAULT_EXPIRY_DAYS: number | null = null

const formatDate = (iso: string | null, locale: string): string => {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(locale)
}

/** Settings → Integrations → API keys. Personal access keys for
 *  external agents (Claude Code, Codex, WorkBuddy, …): each one is
 *  presented as a bearer token and acts as the user who minted it.
 *
 *  Self-contained (own fetch/save), rendered as the first card of the
 *  Integrations tab — it is what MCP clients need. */
export function ApiTokensPanel({ authRequired }: ApiTokensPanelProps) {
  const { t, i18n } = useTranslation()
  const cardRef = useRef<HTMLDivElement>(null)
  const [tokens, setTokens] = useState<ApiToken[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [expiryDays, setExpiryDays] = useState<number | null>(DEFAULT_EXPIRY_DAYS)
  const [creating, setCreating] = useState(false)
  /** The plaintext token, held only until the user closes the
   *  "copy it now" dialog — the server keeps just a hash. */
  const [freshToken, setFreshToken] = useState<string | null>(null)
  /** The token a revoke is pending for, so the confirm step is
   *  attached to the row the user clicked. */
  const [pendingRevoke, setPendingRevoke] = useState<ApiToken | null>(null)
  const [revoking, setRevoking] = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      setTokens(await apiTokens.list())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Old links to the retired API-tokens settings tab land here with
  // #api-keys (see router.tsx) — bring the card into view for them.
  useEffect(() => {
    if (window.location.hash === '#api-keys') {
      cardRef.current?.scrollIntoView()
    }
  }, [])

  const create = async () => {
    if (!name.trim() || creating) return
    setCreating(true)
    setError(null)
    try {
      const created = await apiTokens.create(name.trim(), expiryDays)
      setShowCreate(false)
      setName('')
      setExpiryDays(DEFAULT_EXPIRY_DAYS)
      // The plaintext is shown once and then dropped from memory.
      setFreshToken(created.token)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (token: ApiToken) => {
    setRevoking(true)
    setError(null)
    try {
      await apiTokens.revoke(token.id)
      setPendingRevoke(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRevoking(false)
    }
  }

  return (
    <>
      <div
        ref={cardRef}
        id="api-keys"
        data-testid="api-keys-card"
        className="bg-white rounded-lg border border-gray-200 p-4"
      >
        <div className="flex items-center justify-between mb-1">
          <h3 className="font-semibold text-sm">{t('apiTokens.title')}</h3>
          <button
            data-testid="api-tokens-create"
            onClick={() => setShowCreate((v) => !v)}
            className="px-3 py-1.5 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700"
          >
            + {t('apiTokens.newToken')}
          </button>
        </div>
        <p className="text-xs text-gray-500">{t('apiTokens.description')}</p>

        {!authRequired && (
          <p
            data-testid="api-tokens-auth-off-note"
            className="mt-3 p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800"
          >
            {t('apiTokens.authNotRequiredNote')}
          </p>
        )}

        {error && (
          <p data-testid="api-tokens-error" className="mt-3 text-xs text-red-600">
            {error}
          </p>
        )}

        {showCreate && (
          <div className="mt-4 p-3 bg-gray-50 rounded-lg border border-gray-200 space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                {t('apiTokens.name')}
              </label>
              <input
                data-testid="api-tokens-name"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder={t('apiTokens.namePlaceholder')}
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
                autoFocus
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                {t('apiTokens.expiry')}
              </label>
              <select
                data-testid="api-tokens-expiry"
                value={expiryDays === null ? 'never' : String(expiryDays)}
                onChange={e =>
                  setExpiryDays(
                    e.target.value === 'never' ? null : Number(e.target.value),
                  )
                }
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm bg-white"
              >
                {EXPIRY_CHOICES.map(c => (
                  <option
                    key={c.labelKey}
                    value={c.days === null ? 'never' : String(c.days)}
                  >
                    {t(c.labelKey)}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex gap-2">
              <button
                data-testid="api-tokens-submit"
                onClick={create}
                disabled={creating || !name.trim()}
                className="px-3 py-1.5 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
              >
                {creating ? t('common.loading') : t('apiTokens.createToken')}
              </button>
              <button
                onClick={() => { setShowCreate(false); setName('') }}
                className="px-3 py-1.5 bg-gray-200 rounded text-sm"
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        )}

        {loaded && tokens.length === 0 && !showCreate && (
          <p
            data-testid="api-tokens-empty"
            className="text-sm text-gray-400 text-center py-4"
          >
            {t('apiTokens.noTokens')}
          </p>
        )}

        {tokens.length > 0 && (
          <div className="mt-4 space-y-2">
            {tokens.map(token => (
              <div
                key={token.id}
                data-testid={`api-token-row-${token.id}`}
                className="p-3 border border-gray-200 rounded-lg"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{token.name}</p>
                    <p className="text-xs text-gray-400 font-mono break-all">
                      {token.prefix}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      {t('apiTokens.createdAt')}{' '}
                      {formatDate(token.created_at, i18n.language)}
                      {' · '}
                      {t('apiTokens.lastUsedAt')}{' '}
                      {token.last_used_at
                        ? formatDate(token.last_used_at, i18n.language)
                        : t('apiTokens.never')}
                      {' · '}
                      {t('apiTokens.expiresAt')}{' '}
                      {token.expires_at
                        ? formatDate(token.expires_at, i18n.language)
                        : t('apiTokens.never')}
                    </p>
                  </div>
                  <button
                    data-testid={`api-token-revoke-${token.id}`}
                    onClick={() => setPendingRevoke(token)}
                    className="shrink-0 px-2 py-1 text-xs border border-red-300 text-red-600 rounded hover:bg-red-50"
                  >
                    {t('apiTokens.revoke')}
                  </button>
                </div>

                {pendingRevoke?.id === token.id && (
                  <div
                    data-testid="api-token-revoke-confirm"
                    className="mt-3 p-3 rounded-lg bg-red-50 border border-red-200"
                  >
                    <p className="text-xs text-red-800">
                      {t('apiTokens.revokeConfirm', { name: token.name })}
                    </p>
                    <div className="mt-2 flex gap-2">
                      <button
                        data-testid="api-token-revoke-confirm-button"
                        onClick={() => revoke(token)}
                        disabled={revoking}
                        className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
                      >
                        {t('apiTokens.revoke')}
                      </button>
                      <button
                        data-testid="api-token-revoke-cancel"
                        onClick={() => setPendingRevoke(null)}
                        className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                      >
                        {t('common.cancel')}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {freshToken && (
        <ApiTokenCreatedModal
          token={freshToken}
          onClose={() => setFreshToken(null)}
        />
      )}
    </>
  )
}

import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'

interface AdsConfig {
  host: string
  configured: boolean
}

interface AdsStoreRow {
  local_id: string
  name: string
  marketplace: string | null
  store_key: string | null
  authorized: boolean
}

interface AuthUrlResponse {
  url: string
  expires_in: number
  /** 'ziniao' when the consent link must be opened inside the
   *  anti-detect browser's store window — that is where the seller's
   *  own Amazon session lives. Opening it anywhere else authorizes
   *  whichever account the operator happens to be signed into. */
  open_in: 'ziniao' | 'browser'
}

export function AmazonAdsPanel() {
  const { t } = useTranslation()
  const [config, setConfig] = useState<AdsConfig | null>(null)
  const [stores, setStores] = useState<AdsStoreRow[]>([])
  const [host, setHost] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [link, setLink] = useState<(AuthUrlResponse & { store: string; storeId: string }) | null>(null)
  const [copied, setCopied] = useState(false)

  /** Copy to the clipboard, falling back to a selected-text copy when the
   *  async Clipboard API is refused (some embedded browsers do). Returns
   *  whether it worked so the UI never claims a copy that did not happen. */
  const copyText = async (text: string): Promise<boolean> => {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      const el = document.querySelector<HTMLInputElement>('[data-testid="ads-auth-url"]')
      if (!el) return false
      el.focus()
      el.select()
      try { return document.execCommand('copy') } catch { return false }
    }
  }

  const refresh = async (): Promise<AdsStoreRow[]> => {
    setError(null)
    try {
      const c = (await api.get('/api/ads/config')) as AdsConfig
      setConfig(c)
      setHost(c.host)
      if (c.configured) {
        const body = (await api.post('/api/ads/stores/sync', {})) as { stores: AdsStoreRow[] }
        const rows = body.stores ?? []
        setStores(rows)
        return rows
      }
      setStores([])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    return []
  }

  /** "I've authorized it": look now, and say what we found. The consent
   *  finishes on the service's own domain and nothing calls back here, so
   *  this click is the only moment the page can learn the outcome — it
   *  must report it, not silently redraw a list. */
  const [checkNote, setCheckNote] = useState<string | null>(null)
  const confirmAuthorized = async () => {
    if (!link) return
    setBusy(true); setCheckNote(null)
    try {
      const rows = await refresh()
      const markets = rows
        .filter(r => r.local_id === link.storeId && r.authorized)
        .map(r => r.marketplace)
        .filter(Boolean)
        .sort()
      if (markets.length) {
        setLink(null)
        setMessage(t('ads.authorizedDone', { store: link.store, markets: markets.join(' · ') }))
      } else {
        setCheckNote(t('ads.notYetAuthorized'))
      }
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const save = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.put('/api/ads/config', { host, api_key: apiKey || null })
      setApiKey('')
      setMessage(t('ads.saved'))
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const authorize = async (storeId: string, storeName: string) => {
    setBusy(true); setError(null); setLink(null); setCopied(false); setCheckNote(null); setMessage(null)
    try {
      const body = (await api.get(
        `/api/ads/stores/${storeId}/auth-url`
      )) as AuthUrlResponse
      setLink({ ...body, store: storeName, storeId })
      // The next thing anyone does with this link is paste it into the
      // store's browser window, so copy it now rather than make them
      // select a 300-character URL by hand. Deferred one frame so the
      // fallback path can find the input it selects from.
      await new Promise(r => requestAnimationFrame(() => r(null)))
      setCopied(await copyText(body.url))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** One row per (store, marketplace). A store advertising on two
   *  marketplaces is two rows — an Ads profile is per marketplace. */
  const byStore = stores.reduce<Record<string, AdsStoreRow[]>>((acc, row) => {
    (acc[row.local_id] ??= []).push(row)
    return acc
  }, {})

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h3 className="text-sm font-semibold">{t('ads.title')}</h3>
        <p className="text-xs text-muted-foreground">{t('ads.blurb')}</p>

        <div className="space-y-2">
          <label className="block text-xs font-medium">{t('ads.host')}</label>
          <input
            data-testid="ads-host"
            className="w-full rounded border px-2 py-1 text-sm"
            placeholder="https://ads.example.com"
            value={host}
            onChange={e => setHost(e.target.value)}
          />
          <label className="block text-xs font-medium">{t('ads.apiKey')}</label>
          <input
            data-testid="ads-api-key"
            type="password"
            className="w-full rounded border px-2 py-1 text-sm"
            placeholder={config?.configured ? t('ads.keySet') : 'vas_…'}
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
          />
          <div>
            <button
              data-testid="ads-save"
              disabled={busy}
              onClick={save}
              className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {t('ads.save')}
            </button>
          </div>
        </div>
      </section>

      {config?.configured && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold">{t('ads.stores')}</h4>
          <table className="w-full text-sm" data-testid="ads-stores">
            <tbody>
              {Object.entries(byStore).map(([localId, rows]) => {
                const authorized = rows.filter(r => r.authorized)
                return (
                  <tr key={localId} className="border-t">
                    <td className="py-2">{rows[0].name}</td>
                    <td className="py-2 text-xs text-muted-foreground">
                      {authorized.length
                        ? authorized.map(r => r.marketplace).join(' · ')
                        : t('ads.notAuthorized')}
                    </td>
                    <td className="py-2 text-right">
                      <button
                        data-testid={`ads-authorize-${localId}`}
                        disabled={busy}
                        onClick={() => authorize(localId, rows[0].name)}
                        className="rounded border px-2 py-1 text-xs disabled:opacity-50"
                      >
                        {authorized.length ? t('ads.reauthorize') : t('ads.authorize')}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>
      )}

      {link && (
        <section
          data-testid="ads-auth-link"
          className="space-y-2 rounded border p-3"
        >
          <h4 className="text-sm font-semibold">
            {t('ads.authorizeStore', { store: link.store })}
          </h4>
          <p className="text-xs">
            {link.open_in === 'ziniao'
              ? t('ads.openInZiniao')
              : t('ads.openHere')}
          </p>
          {/* Its own line, in red: a wrong-account authorization looks like
              a success and files another seller's spend under this store,
              so it must not read as a footnote. */}
          <p data-testid="ads-wrong-account" className="text-xs font-medium text-red-600">
            {link.open_in === 'ziniao'
              ? t('ads.wrongAccountZiniao')
              : t('ads.wrongAccountHere')}
          </p>
          <div className="flex gap-2">
            <input
              data-testid="ads-auth-url"
              readOnly
              className="min-w-0 flex-1 rounded border px-2 py-1 font-mono text-xs"
              value={link.url}
              onFocus={e => e.currentTarget.select()}
            />
            <button
              data-testid="ads-copy-url"
              onClick={async () => setCopied(await copyText(link.url))}
              className="shrink-0 rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-700"
            >
              {copied ? t('ads.copied') : t('ads.copy')}
            </button>
          </div>
          {copied && (
            <p data-testid="ads-copied-note" className="text-xs text-green-700">
              {t('ads.copiedNote')}
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            {t('ads.linkExpires', { minutes: Math.round(link.expires_in / 60) })}
          </p>
          <button
            data-testid="ads-recheck"
            disabled={busy}
            onClick={confirmAuthorized}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? t('ads.checking') : t('ads.recheck')}
          </button>
          {checkNote && (
            <p data-testid="ads-check-note" className="text-xs font-medium text-amber-700">
              {checkNote}
            </p>
          )}
        </section>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}
      {message && <p data-testid="ads-message" className="text-xs font-medium text-green-700">{message}</p>}
    </div>
  )
}

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
  const [link, setLink] = useState<(AuthUrlResponse & { store: string }) | null>(null)

  const refresh = async () => {
    setError(null)
    try {
      const c = (await api.get('/api/ads/config')) as AdsConfig
      setConfig(c)
      setHost(c.host)
      if (c.configured) {
        const body = (await api.post('/api/ads/stores/sync', {})) as { stores: AdsStoreRow[] }
        setStores(body.stores ?? [])
      } else {
        setStores([])
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
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
    setBusy(true); setError(null); setLink(null)
    try {
      const body = (await api.get(
        `/api/ads/stores/${storeId}/auth-url`
      )) as AuthUrlResponse
      setLink({ ...body, store: storeName })
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
              className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
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
          <input
            data-testid="ads-auth-url"
            readOnly
            className="w-full rounded border px-2 py-1 font-mono text-xs"
            value={link.url}
            onFocus={e => e.currentTarget.select()}
          />
          <p className="text-xs text-muted-foreground">
            {t('ads.linkExpires', { minutes: Math.round(link.expires_in / 60) })}
          </p>
          <button
            data-testid="ads-recheck"
            disabled={busy}
            onClick={refresh}
            className="rounded border px-2 py-1 text-xs disabled:opacity-50"
          >
            {t('ads.recheck')}
          </button>
        </section>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}
      {message && <p className="text-xs text-muted-foreground">{message}</p>}
    </div>
  )
}

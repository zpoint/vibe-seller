import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { revealSecret } from '../api'
import { copyText } from '../lib/clipboard'

/**
 * The admin's "Show / Copy full key" action for one stored secret.
 *
 * Every edit form shows stored secrets only as a mask; this is the
 * only component that fetches the real value — through
 * POST /api/secrets/reveal, which is admin-session-only (an API
 * token is refused there too). Members never see it rendered: gate
 * with the signed-in user's role at the call site, mirroring the
 * server's require_admin.
 *
 * Show toggles the value inline; Copy copies without showing. Both
 * go through the reveal endpoint (the value is never written into
 * the form state, so it cannot be echoed back into a save).
 */
export function SecretReveal({
  resource,
  id,
  field,
  compact = false,
}: {
  resource: string
  /** Empty for the singleton resources (vision, dida365). */
  id?: string
  field: string
  /** Tighter padding for inline placement next to an input. */
  compact?: boolean
}) {
  const { t } = useTranslation()
  const [shown, setShown] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const reveal = async (): Promise<string | null> => {
    setError('')
    try {
      const res = await revealSecret(resource, id ?? '', field)
      return res.value ?? null
    } catch (e) {
      setError(e instanceof Error ? e.message : t('secrets.revealFailed'))
      return null
    }
  }

  const onShow = async () => {
    if (shown !== null) {
      setShown(null)
      return
    }
    setBusy(true)
    try {
      const value = await reveal()
      if (value !== null) setShown(value)
    } finally {
      setBusy(false)
    }
  }

  const onCopy = async () => {
    setBusy(true)
    setCopied(false)
    try {
      const value = await reveal()
      if (value === null) return
      const ok = await copyText(value)
      if (ok) {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      } else {
        setError(t('secrets.copyFailed'))
      }
    } finally {
      setBusy(false)
    }
  }

  const pad = compact ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'
  return (
    <span
      className="inline-flex flex-wrap items-center gap-1.5 align-middle"
      data-testid={`secret-reveal-${resource}-${field}`}
    >
      <button
        type="button"
        onClick={onShow}
        disabled={busy}
        className={`${pad} rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40`}
      >
        {shown !== null ? t('secrets.hideValue') : t('secrets.showValue')}
      </button>
      <button
        type="button"
        onClick={onCopy}
        disabled={busy}
        className={`${pad} rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40`}
      >
        {copied ? t('secrets.copied') : t('secrets.copyValue')}
      </button>
      {shown !== null && (
        <code
          data-testid={`secret-revealed-${resource}-${field}`}
          className="px-1.5 py-0.5 rounded bg-amber-50 border border-amber-200 text-amber-800 font-mono text-[11px] break-all max-w-[24rem]"
        >
          {shown}
        </code>
      )}
      {error && <span className="text-[11px] text-red-600">{error}</span>}
    </span>
  )
}

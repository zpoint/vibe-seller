import { useState } from 'react'
import { useTranslation } from 'react-i18next'

/** This installation's id, under the version. Stable across upgrades; a
 *  person quotes it to support, and the ads service files the
 *  installation under the same id. Not a secret — it opens nothing. */
export function InstallIdBadge({ installId }: { installId: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  if (!installId) return null
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(installId)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* the full id is in the tooltip */ }
  }
  return (
    <button
      type="button"
      data-testid="install-id"
      onClick={copy}
      title={`${t('sidebar.installId')}: ${installId} — ${t('sidebar.installIdCopy')}`}
      className="-mt-1 mb-1 block font-mono text-[10px] text-gray-400 hover:text-gray-600"
    >
      {copied ? t('sidebar.installIdCopied') : `ID ${installId.slice(0, 8)}`}
    </button>
  )
}

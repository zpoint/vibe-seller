/**
 * Inline render of an ``external_config_override`` task failure.
 *
 * The backend writes ``task.error`` as the JSON-serialised
 * ``ExternalConfigOverrideDetail`` so the frontend can render it
 * in the user's locale via the shared
 * ``errors.externalConfigOverride.*`` i18n keys. Falls back to
 * raw text when the JSON is malformed (defensive against older
 * task rows from before this format).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  isExternalConfigOverrideDetail,
  type ExternalConfigOverrideDetail,
} from './externalConfigOverrideDetail'

function parseDetail(raw: string): ExternalConfigOverrideDetail | null {
  try {
    const parsed = JSON.parse(raw)
    return isExternalConfigOverrideDetail(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function ExternalConfigOverrideErrorCard({ error }: { error: string }) {
  const { t } = useTranslation()
  const detail = parseDetail(error)
  const [copied, setCopied] = useState(false)

  // Defensive: legacy task rows with the old plain-text error
  // still need to render something useful.
  if (!detail) {
    return (
      <div className="mt-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded whitespace-pre-line">
        {error}
      </div>
    )
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(detail.clear_command)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard blocked */
    }
  }

  return (
    <div
      className="mt-2 text-sm text-red-700 bg-red-50 border border-red-200 px-3 py-3 rounded"
      data-testid="external-config-override-error"
    >
      <div className="font-medium">
        {t('errors.externalConfigOverride.title')}
      </div>
      <p className="mt-2 text-gray-800">
        {t('errors.externalConfigOverride.intro', {
          profileId: detail.profile_id,
          settingsPath: detail.settings_path,
        })}
      </p>
      <ul className="mt-1 ml-5 list-disc text-xs font-mono text-gray-800">
        {detail.overriding_keys.map(k => <li key={k}>{k}</li>)}
      </ul>
      <p className="mt-3 font-medium text-gray-900">
        {t('errors.externalConfigOverride.pickOne')}
      </p>
      <ol className="mt-1 ml-5 list-decimal space-y-2 text-gray-800">
        <li>{t('errors.externalConfigOverride.optionDefault')}</li>
        <li>
          {t('errors.externalConfigOverride.optionClear')}
          <pre className="mt-2 max-h-40 overflow-auto rounded bg-gray-100 p-2 text-xs font-mono text-gray-900 whitespace-pre-wrap">
            {detail.clear_command}
          </pre>
        </li>
      </ol>
      <button
        onClick={copy}
        className="mt-2 rounded border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 bg-white hover:bg-gray-50"
      >
        {copied
          ? t('errors.externalConfigOverride.copied')
          : t('errors.externalConfigOverride.copyCommand')}
      </button>
    </div>
  )
}

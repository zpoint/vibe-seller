import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ExternalConfigOverrideDetail } from './externalConfigOverrideDetail'

type Props = {
  detail: ExternalConfigOverrideDetail
  onUseDefault?: () => void
  onClose: () => void
}

export function ExternalConfigOverrideModal({
  detail,
  onUseDefault,
  onClose,
}: Props) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(detail.clear_command)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard blocked — user can still select+copy manually */
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="ext-override-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-2xl rounded-lg bg-white p-6 shadow-xl">
        <h2
          id="ext-override-title"
          className="text-lg font-semibold text-gray-900"
        >
          {t('errors.externalConfigOverride.title')}
        </h2>
        <p className="mt-3 text-sm text-gray-700">
          {t('errors.externalConfigOverride.intro', {
            profileId: detail.profile_id,
            settingsPath: detail.settings_path,
          })}
        </p>
        <ul className="mt-2 ml-5 list-disc text-sm font-mono text-gray-800">
          {detail.overriding_keys.map(k => (
            <li key={k}>{k}</li>
          ))}
        </ul>

        <p className="mt-4 text-sm font-medium text-gray-900">
          {t('errors.externalConfigOverride.pickOne')}
        </p>
        <ol className="mt-2 ml-5 list-decimal space-y-2 text-sm text-gray-800">
          <li>{t('errors.externalConfigOverride.optionDefault')}</li>
          <li>
            {t('errors.externalConfigOverride.optionClear')}
            <pre className="mt-2 max-h-40 overflow-auto rounded bg-gray-100 p-3 text-xs font-mono text-gray-900">
              {detail.clear_command}
            </pre>
          </li>
        </ol>

        <div className="mt-5 flex flex-wrap gap-2">
          {onUseDefault && (
            <button
              onClick={onUseDefault}
              className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            >
              {t('errors.externalConfigOverride.useDefault')}
            </button>
          )}
          <button
            onClick={copy}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            {copied
              ? t('errors.externalConfigOverride.copied')
              : t('errors.externalConfigOverride.copyCommand')}
          </button>
          <button
            onClick={onClose}
            className="ml-auto rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            {t('errors.externalConfigOverride.close')}
          </button>
        </div>
      </div>
    </div>
  )
}

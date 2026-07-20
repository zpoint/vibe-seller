import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'

interface VisionConfig {
  kie_api_key_set: boolean
  kie_api_key_masked: string
  models?: string[]
  default_model?: string
}

interface VisionPanelProps {
  isAdmin: boolean
}

/** Settings → AI → Vision. Configure the kie.ai image-generation key.
 *  Self-contained (own fetch/save), mirroring the other settings panels. */
export function VisionPanel({ isAdmin }: VisionPanelProps) {
  const { t } = useTranslation()
  const [config, setConfig] = useState<VisionConfig | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = async () => {
    try {
      setConfig(await api.get('/api/vision/config'))
    } catch { /* not configured / no access */ }
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await api.put('/api/vision/config', { kie_api_key: keyInput.trim() })
      setKeyInput('')
      setSaved(true)
      await load()
    } catch { /* 403 for non-admins; leave UI unchanged */ } finally {
      setSaving(false)
    }
  }

  return (
    <div
      data-testid="vision-panel"
      className="bg-white rounded-lg border border-gray-200 p-4"
    >
      <div className="flex items-center justify-between mb-1">
        <h3 className="font-semibold text-sm">{t('settings.visionConfig')}</h3>
        <span
          data-testid="vision-key-status"
          className={`px-2 py-1 text-xs rounded-full ${
            config?.kie_api_key_set
              ? 'bg-green-100 text-green-700'
              : 'bg-gray-100 text-gray-500'
          }`}
        >
          {config?.kie_api_key_set
            ? `${t('settings.visionKeySet')} ${config.kie_api_key_masked}`
            : t('settings.visionKeyMissing')}
        </span>
      </div>
      <p className="text-xs text-gray-500 mb-3">{t('settings.visionHint')}</p>

      {isAdmin ? (
        <div className="flex gap-2">
          <input
            data-testid="vision-key-input"
            type="password"
            value={keyInput}
            onChange={e => setKeyInput(e.target.value)}
            placeholder={t('settings.visionKeyPlaceholder')}
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          />
          <button
            data-testid="vision-key-save"
            onClick={save}
            disabled={saving || !keyInput.trim()}
            className="px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-40"
          >
            {t('common.save')}
          </button>
        </div>
      ) : (
        <p className="text-xs text-gray-400">{t('settings.visionAdminOnly')}</p>
      )}
      {saved && (
        <p data-testid="vision-saved" className="text-xs text-green-600 mt-2">
          {t('settings.visionSaved')}
        </p>
      )}
    </div>
  )
}

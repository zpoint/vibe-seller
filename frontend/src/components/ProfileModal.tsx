import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'

interface Profile {
  id: string
  name: string
  description: string
  env: Record<string, string>
  load_global_mcp?: boolean
}

interface ProviderPreset {
  name: string
  description: string
  env: Record<string, string>
  load_global_mcp?: boolean
}

interface ProfileModalProps {
  isOpen: boolean
  onClose: () => void
  onSave: (profile: Profile) => void
  editingProfile?: Profile
}

export function ProfileModal({
  isOpen,
  onClose,
  onSave,
  editingProfile,
}: ProfileModalProps) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <ProfileForm
        key={editingProfile?.id ?? '__new__'}
        editingProfile={editingProfile}
        onClose={onClose}
        onSave={onSave}
      />
    </div>
  )
}

/** Inner form component — remounted via key prop when profile changes. */
function ProfileForm({
  editingProfile,
  onClose,
  onSave,
}: {
  editingProfile?: Profile
  onClose: () => void
  onSave: (profile: Profile) => void
}) {
  const { t } = useTranslation()

  const [name, setName] = useState(editingProfile?.name ?? '')
  const [envVars, setEnvVars] = useState<{ key: string; value: string }[]>(
    editingProfile
      ? Object.entries(editingProfile.env).map(([k, v]) => ({ key: k, value: v }))
      : []
  )
  const [loadGlobalMcp, setLoadGlobalMcp] = useState(
    editingProfile?.load_global_mcp ?? false
  )
  const [presets, setPresets] = useState<Record<string, ProviderPreset>>({})
  const [selectedPreset, setSelectedPreset] = useState('')
  const [error, setError] = useState('')

  // Fetch provider presets once on mount
  useEffect(() => {
    let cancelled = false
    fetch('/api/profiles/presets', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) setPresets(data.presets || {})
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  const applyPreset = (presetId: string) => {
    const preset = presets[presetId]
    if (!preset) return

    setSelectedPreset(presetId)
    setName(preset.name)
    setLoadGlobalMcp(preset.load_global_mcp ?? false)

    // Fill all env vars from preset + empty ANTHROPIC_AUTH_TOKEN row
    const vars = Object.entries(preset.env).map(([k, v]) => ({ key: k, value: v }))
    vars.push({ key: 'ANTHROPIC_AUTH_TOKEN', value: '' })
    setEnvVars(vars)
  }

  const addEnvVar = () => {
    setEnvVars([...envVars, { key: '', value: '' }])
  }

  const updateEnvKey = (idx: number, newKey: string) => {
    const updated = [...envVars]
    updated[idx].key = newKey
    setEnvVars(updated)
  }

  const updateEnvValue = (idx: number, newValue: string) => {
    const updated = [...envVars]
    updated[idx].value = newValue
    setEnvVars(updated)
  }

  const removeEnv = (idx: number) => {
    setEnvVars(envVars.filter((_, i) => i !== idx))
  }

  const handleSave = () => {
    if (!name.trim()) {
      setError(t('profiles.nameRequired') || 'Profile name is required')
      return
    }

    const env: Record<string, string> = {}
    envVars.forEach(({ key, value }) => {
      if (key.trim()) {
        env[key.trim()] = value
      }
    })

    onSave({
      id: editingProfile?.id || name.toLowerCase().replace(/[^a-z0-9]/g, '-'),
      name: name.trim(),
      description: '',
      env,
      load_global_mcp: loadGlobalMcp,
    })

    setError('')
    onClose()
  }

  return (
    <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
      <div className="p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold">
            {editingProfile
              ? t('profiles.editTitle')
              : t('profiles.createTitle')}
          </h2>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 text-2xl leading-none"
          >
            &times;
          </button>
        </div>

        <p className="text-sm text-gray-500 mb-2">{t('profiles.modalHint')}</p>

        {error && (
          <div className="mb-4 p-3 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}

        <div className="space-y-4">
          {/* Provider preset selector — only when creating */}
          {!editingProfile && Object.keys(presets).length > 0 && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('profiles.envPresets')}
              </label>
              <div className="flex flex-wrap gap-2">
                {Object.entries(presets).map(([id, preset]) => (
                  <button
                    key={id}
                    onClick={() => applyPreset(id)}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      selectedPreset === id
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                    }`}
                  >
                    {preset.name}
                  </button>
                ))}
                <button
                  onClick={() => {
                    setSelectedPreset('custom')
                    setName('')
                    setEnvVars([])
                    setLoadGlobalMcp(false)
                  }}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    selectedPreset === 'custom'
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {t('profiles.custom')}
                </button>
              </div>
            </div>
          )}

          {/* Profile Name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('profiles.profileName')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              placeholder={t('profiles.namePlaceholder')}
            />
          </div>

          {/* Load Global MCP toggle */}
          <div className="flex items-center gap-3">
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={loadGlobalMcp}
                onChange={(e) => setLoadGlobalMcp(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600" />
            </label>
            <div>
              <span className="text-sm font-medium text-gray-700">
                {t('profiles.loadGlobalMcp')}
              </span>
              <p className="text-xs text-gray-500">
                {t('profiles.loadGlobalMcpHint')}
              </p>
            </div>
          </div>

          {/* Environment Variables */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-medium text-gray-700">
                {t('profiles.environmentVariables')}
              </h4>
              <button
                onClick={addEnvVar}
                className="text-sm text-blue-600 hover:text-blue-700 font-medium"
              >
                + {t('profiles.addVariable')}
              </button>
            </div>

            {envVars.length === 0 ? (
              <p className="text-sm text-gray-500 italic">
                {t('profiles.noVariables')}
              </p>
            ) : (
              <div className="space-y-2">
                {envVars.map((env, idx) => (
                  <div key={idx} className="flex gap-2">
                    <input
                      type="text"
                      placeholder="KEY"
                      value={env.key}
                      onChange={(e) => updateEnvKey(idx, e.target.value)}
                      className="flex-1 px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    />
                    <input
                      type="text"
                      placeholder="value"
                      value={env.value}
                      onChange={(e) => updateEnvValue(idx, e.target.value)}
                      className="flex-[2] px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    />
                    <button
                      onClick={() => removeEnv(idx)}
                      className="px-3 py-2 text-red-600 hover:text-red-700 hover:bg-red-50 rounded-lg transition-colors"
                      title={t('common.remove')}
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4 border-t">
            <button
              onClick={onClose}
              className="px-4 py-2 text-gray-700 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={handleSave}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
            >
              {editingProfile
                ? t('common.save')
                : t('common.create')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

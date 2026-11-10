import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

/** Provider-preset picker for the AI profile modal (create flow only).
 *
 *  Extracted from ProfileModal.tsx to keep that file under the
 *  800-line limit. Grouped presets (e.g. "Alibaba Cloud", "GLM")
 *  collapse into one top-level button that reveals its variant
 *  sub-row; everything else renders as a standalone button.
 */

export interface PresetEntry {
  kind: 'preset' | 'group'
  id?: string
  label?: string
  group?: string
  groupLabel?: string
}

/** Top-level provider entries: standalone presets stay as-is; presets
 *  sharing a `group` collapse into one entry (first occurrence wins
 *  ordering). */
function useProviderEntries(
  presets: Record<
    string,
    { name: string; group?: string; groupLabel?: string }
  >,
): PresetEntry[] {
  return useMemo(() => {
    const seen = new Set<string>()
    const entries: PresetEntry[] = []
    for (const [id, p] of Object.entries(presets)) {
      if (p.group) {
        if (!seen.has(p.group)) {
          seen.add(p.group)
          entries.push({
            kind: 'group',
            group: p.group,
            groupLabel: p.groupLabel || p.group,
          })
        }
      } else {
        entries.push({ kind: 'preset', id, label: p.name })
      }
    }
    return entries
  }, [presets])
}

export function ProfileModalPresets({
  presets,
  selectedPreset,
  selectedGroup,
  setSelectedGroup,
  applyPreset,
  selectCustom,
}: {
  presets: Record<
    string,
    { name: string; group?: string; groupLabel?: string; variant?: string }
  >
  selectedPreset: string
  selectedGroup: string
  setSelectedGroup: (group: string) => void
  applyPreset: (presetId: string) => void
  selectCustom: () => void
}) {
  const { t } = useTranslation()
  const providerEntries = useProviderEntries(presets)

  const groupVariants = (group: string) =>
    Object.entries(presets)
      .filter(([, p]) => p.group === group)
      .map(([id, p]) => ({ id, label: p.variant || p.name }))

  const btn = (active: boolean) =>
    `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
      active
        ? 'bg-indigo-600 text-white'
        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
    }`

  return (
    <div data-testid="profile-modal-presets">
      <label className="block text-sm font-medium text-gray-700 mb-1">
        {t('profiles.envPresets')}
      </label>
      <div className="flex flex-wrap gap-2">
        {providerEntries.map((entry) =>
          entry.kind === 'preset' ? (
            <button
              key={entry.id}
              type="button"
              onClick={() => applyPreset(entry.id!)}
              className={btn(selectedPreset === entry.id)}
            >
              {entry.label}
            </button>
          ) : (
            <button
              key={entry.group}
              type="button"
              onClick={() => {
                // Open the group AND apply its first variant so the
                // fields populate immediately (rather than leaving the
                // previously-selected provider's values in place).
                // Re-clicking an already-open group is a no-op so a
                // typed key isn't wiped.
                if (selectedGroup === entry.group) return
                const first = groupVariants(entry.group!)[0]
                if (first) applyPreset(first.id)
                else setSelectedGroup(entry.group!)
              }}
              className={btn(selectedGroup === entry.group)}
            >
              {entry.groupLabel} ›
            </button>
          )
        )}
        <button
          type="button"
          onClick={selectCustom}
          className={btn(selectedPreset === 'custom')}
        >
          {t('profiles.custom')}
        </button>
      </div>

      {/* Variant sub-row for the selected group */}
      {selectedGroup && (
        <div className="flex flex-wrap gap-2 mt-2 pl-3 border-l-2 border-indigo-200">
          {groupVariants(selectedGroup).map((v) => (
            <button
              key={v.id}
              type="button"
              onClick={() => applyPreset(v.id)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                selectedPreset === v.id
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

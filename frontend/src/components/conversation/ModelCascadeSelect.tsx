import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ImageModelOption } from '../../types'

interface ModelCascadeSelectProps {
  models: ImageModelOption[]
  value: string
  disabled?: boolean
  onChange: (id: string) => void
}

/** Two-level (left→right) cascading model picker: providers in the left
 *  column, and the hovered/active provider's models in the right column.
 *  Within each provider the models are sorted most-expensive-first.
 *
 *  A native <select> can't render a left→right submenu, so this is a
 *  custom popover. The closed trigger shows the selected model + its
 *  price; opening reveals the two columns. */
export function ModelCascadeSelect({
  models,
  value,
  disabled,
  onChange,
}: ModelCascadeSelectProps) {
  const { t, i18n } = useTranslation()
  const zh = (i18n?.language || '').startsWith('zh')
  const unit = t('vision.perImageUnit')
  const price = (m: ImageModelOption): string => {
    if (!m.usd && !m.cny) return ''
    return zh ? `≈ ¥${m.cny}/${unit}` : `≈ $${m.usd}/${unit}`
  }

  const list = models.length ? models : []
  const selected = list.find(m => m.id === value) ?? list[0]
  const providers = [...new Set(list.map(m => m.provider))]
  // Most expensive first within each provider (sorted in the frontend).
  const modelsFor = (p: string) =>
    list.filter(m => m.provider === p).sort((a, b) => b.usd - a.usd)

  const [open, setOpen] = useState(false)
  const [activeProvider, setActiveProvider] = useState(
    selected?.provider ?? providers[0],
  )
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const toggle = () => {
    if (disabled) return
    // Open focused on the selected model's provider column.
    setActiveProvider(selected?.provider ?? providers[0])
    setOpen(o => !o)
  }
  const pick = (id: string) => {
    onChange(id)
    setOpen(false)
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        data-testid="image-model-select"
        disabled={disabled}
        onClick={toggle}
        aria-haspopup="true"
        aria-expanded={open}
        className="w-full flex items-center justify-between px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white text-left disabled:bg-gray-50 disabled:text-gray-400"
      >
        <span className="truncate text-gray-900">
          {selected?.label}
          {selected && price(selected) && (
            <span className="text-gray-500"> · {price(selected)}</span>
          )}
        </span>
        <span className="ml-2 shrink-0 text-gray-400">▾</span>
      </button>

      {open && (
        <div
          data-testid="image-model-menu"
          className="absolute left-0 z-30 mt-1 flex max-h-72 rounded-lg border border-gray-200 bg-white shadow-lg overflow-hidden"
        >
          {/* Level 1 — providers */}
          <ul className="w-40 shrink-0 overflow-y-auto border-r border-gray-100 py-1">
            {providers.map(p => (
              <li key={p}>
                <button
                  type="button"
                  data-testid={`image-provider-${p}`}
                  onMouseEnter={() => setActiveProvider(p)}
                  onFocus={() => setActiveProvider(p)}
                  onClick={() => setActiveProvider(p)}
                  className={`w-full flex items-center justify-between px-3 py-1.5 text-sm text-left ${
                    p === activeProvider
                      ? 'bg-indigo-50 text-indigo-800 font-medium'
                      : 'text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <span className="truncate">{p || '—'}</span>
                  <span className="ml-2 shrink-0 text-gray-300">›</span>
                </button>
              </li>
            ))}
          </ul>
          {/* Level 2 — models for the active provider, most expensive first */}
          <ul
            data-testid="image-model-submenu"
            className="w-72 shrink-0 overflow-y-auto py-1"
          >
            {modelsFor(activeProvider).map(m => (
              <li key={m.id}>
                <button
                  type="button"
                  data-model-id={m.id}
                  onClick={() => pick(m.id)}
                  className={`w-full flex items-center justify-between gap-3 px-3 py-1.5 text-sm text-left ${
                    m.id === value
                      ? 'bg-indigo-100 text-indigo-900'
                      : 'text-gray-800 hover:bg-gray-50'
                  }`}
                >
                  <span className="truncate">{m.label}</span>
                  {price(m) && (
                    <span className="shrink-0 tabular-nums text-gray-500">
                      {price(m)}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

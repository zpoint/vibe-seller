import { useMemo } from 'react'

interface TimezoneSelectProps {
  value: string
  onChange: (tz: string) => void
  className?: string
  disabled?: boolean
}

function computeZones(currentValue: string): string[] {
  const anyIntl = Intl as typeof Intl & {
    supportedValuesOf?: (key: string) => string[]
  }
  let base: string[] = []
  if (typeof anyIntl.supportedValuesOf === 'function') {
    try {
      const zones = anyIntl.supportedValuesOf('timeZone')
      if (Array.isArray(zones) && zones.length > 0) base = zones
    } catch {
      /* fall through to browser-zone fallback */
    }
  }
  if (base.length === 0) {
    const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (browserZone) base = [browserZone]
  }
  // Always include the controlled value so the <select> never has a
  // value with no matching <option> (React warns + picks the first item).
  if (currentValue && !base.includes(currentValue)) {
    return [currentValue, ...base]
  }
  return base
}

export function TimezoneSelect({
  value,
  onChange,
  className,
  disabled,
}: TimezoneSelectProps) {
  const zones = useMemo(() => computeZones(value), [value])
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      disabled={disabled}
      className={
        className ||
        'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white'
      }
    >
      {zones.map(tz => (
        <option key={tz} value={tz}>
          {tz}
        </option>
      ))}
    </select>
  )
}

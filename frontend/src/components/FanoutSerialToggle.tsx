import { useTranslation } from 'react-i18next'

interface FanoutSerialToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
}

/**
 * "Run stores one at a time" for an all-stores fanout schedule
 * (`Schedule.fanout_serial`). Shared by the create and edit modals,
 * which only render it when the schedule fans out — the server
 * refuses the flag anywhere else.
 */
export function FanoutSerialToggle({ checked, onChange }: FanoutSerialToggleProps) {
  const { t } = useTranslation()
  return (
    <label
      className="mx-6 mb-4 flex items-start gap-2 p-2 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50"
      data-testid="fanout-serial-toggle"
    >
      <input
        type="checkbox"
        className="mt-1"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
      />
      <div className="flex-1">
        <div className="text-sm font-medium text-gray-900">
          {t('schedules.fanoutSerial')}
        </div>
        <div className="text-xs text-gray-500">
          {t('schedules.fanoutSerialDescription')}
        </div>
      </div>
    </label>
  )
}

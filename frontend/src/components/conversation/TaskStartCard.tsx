import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface TaskStartCardProps {
  title: string
  description?: string
}

export function TaskStartCard({ title, description }: TaskStartCardProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      data-testid="task-start-card"
      className="border-l-4 border-l-indigo-400 bg-[#fafbff] rounded-xl shadow-sm overflow-hidden"
    >
      <div className="px-4 py-2.5 flex items-center gap-2">
        <span className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-indigo-100 text-indigo-700 uppercase tracking-wide">
          {t('tasks.taskStartLabel')}
        </span>
      </div>
      {description ? (
        <button
          type="button"
          aria-expanded={expanded}
          className="w-full text-left px-4 pb-2.5 flex items-center gap-1.5 hover:text-indigo-600"
          onClick={() => setExpanded(e => !e)}
        >
          <span className={`text-[10px] text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
          <span className="text-[15px] font-semibold text-gray-800">{title}</span>
        </button>
      ) : (
        <div className="px-4 pb-2.5 text-[15px] font-semibold text-gray-800">
          {title}
        </div>
      )}
      {description && expanded && (
        <>
          <div className="border-t border-gray-100" />
          <div className="text-[13px] text-gray-500 leading-relaxed px-4 py-2.5">
            {description}
          </div>
        </>
      )}
    </div>
  )
}

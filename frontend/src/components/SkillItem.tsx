import { useTranslation } from 'react-i18next'
import { WsFileItem } from './ui'
import type { WsSkill } from '../types'

/** One skill row in the workspace sidebar tree (expandable file list). */
export function SkillItem(props: {
  skill: WsSkill
  badge: React.ReactNode
  expanded: boolean
  toggleExpanded: () => void
  wsSelectedFile: string | null
  openWsFile: (path: string) => void
  deleteWsFile: (path: string) => void
  onDelete?: (slug: string) => void
}) {
  const { t } = useTranslation()
  const { skill, badge, expanded, toggleExpanded, wsSelectedFile, openWsFile, deleteWsFile, onDelete } = props
  return (
    <div className="border-b border-gray-50">
      <div className="flex items-center px-3">
        <button
          onClick={toggleExpanded}
          className="flex-1 text-left py-1.5 flex items-center gap-2 hover:bg-gray-50 text-sm"
        >
          <span className={`text-[10px] text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`}>&#9654;</span>
          <span className="font-medium text-gray-700 text-xs">{skill.slug}</span>
          {badge}
          <span className="text-[10px] text-gray-400 ml-auto">{skill.file_count}</span>
        </button>
        {onDelete && (
          <button
            type="button"
            onClick={() => onDelete(skill.slug)}
            className="text-gray-300 hover:text-red-500 cursor-pointer ml-1 text-xs"
            title={t('workspace.uninstallSkill')}
            aria-label={t('workspace.uninstallSkill')}
          >&times;</button>
        )}
      </div>
      {skill.description && !expanded && (
        <p className="px-3 pb-1 ml-5 text-[10px] text-gray-400 leading-tight truncate">{skill.description}</p>
      )}
      {expanded && (
        <div className="ml-3">
          {skill.files.map(f => (
            <WsFileItem key={f.path} file={f} selected={wsSelectedFile === f.path} onSelect={openWsFile} onDelete={deleteWsFile} />
          ))}
          {skill.files.length === 0 && (
            <div className="px-4 py-1 text-[10px] text-gray-400 italic">{t('common.noData')}</div>
          )}
        </div>
      )}
    </div>
  )
}

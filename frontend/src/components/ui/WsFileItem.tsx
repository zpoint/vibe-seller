import { useTranslation } from 'react-i18next'
import type { WsFile } from '../../types'

const FILE_HINT_KEYS: Partial<Record<string, string>> = {
  'STORE.md': 'workspace.hintStoreProfile',
  'notes.md': 'workspace.hintNotes',
  'logistics.md': 'workspace.hintLogistics',
  'platform-rules.md': 'workspace.hintPlatformRules',
  'browser-tips.md': 'workspace.hintBrowserTips',
  'SKILL.md': 'workspace.hintSkillMd',
}

export function WsFileItem({ file, selected, onSelect, onDelete, displayPrefix }: { file: WsFile; selected: boolean; onSelect: (path: string) => void; onDelete: (path: string) => void; displayPrefix?: string }) {
  const { t } = useTranslation()
  const hintKey = FILE_HINT_KEYS[file.name]
  const displayName = displayPrefix
    ? (file.path.startsWith(displayPrefix) ? file.path.slice(displayPrefix.length) : file.name)
    : file.name
  return (
    <div
      className={`group px-3 py-1.5 cursor-pointer text-xs ${selected ? 'bg-indigo-50 text-indigo-700' : 'text-gray-600 hover:bg-gray-50'}`}
      onClick={() => onSelect(file.path)}
    >
      <div className="flex items-center">
        <span className="flex-1 truncate">
          <span className="mr-1.5 opacity-50">&#128196;</span>
          {displayName}
        </span>
        {file.size > 0 && (
          <span className="text-[10px] text-gray-400 mr-1 flex-shrink-0">{file.size > 1024 ? `${(file.size / 1024).toFixed(1)}k` : `${file.size}b`}</span>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(file.path) }}
          className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 text-[10px] flex-shrink-0 ml-1"
          title={t('common.delete')}
        >&#10005;</button>
      </div>
      {hintKey && <p className={`mt-0.5 text-[10px] leading-tight ml-5 ${selected ? 'text-indigo-400' : 'text-gray-400'}`}>{t(hintKey)}</p>}
    </div>
  )
}

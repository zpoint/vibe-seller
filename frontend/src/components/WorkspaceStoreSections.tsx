import { useTranslation } from 'react-i18next'
import { WsFileItem } from './ui'
import type { WsFile, WsStoreProfile, WsStructured } from '../types'

interface StoreSectionsProps {
  wsStructured: WsStructured | null
  wsSelectedFile: string | null
  wsExpandedStores: Set<string>
  toggleStoreExpanded: (slug: string) => void
  openWsFile: (path: string) => void
  deleteWsFile: (path: string) => void
  wsNewFileName: string
  setWsNewFileName: (v: string) => void
  wsNewFileSection: string | null
  setWsNewFileSection: (v: string | null) => void
  createWsFile: (section: string, fileName: string) => void
}

/** Group run-data files by parent dir (relative to data_path) and
 *  render each dir as a collapsible group — generic, so date buckets
 *  like ads/<platform>/2026-06/ fold naturally, newest first. */
function StoreDataFolders(p: {
  store: WsStoreProfile
  wsSelectedFile: string | null
  wsExpandedStores: Set<string>
  toggleStoreExpanded: (slug: string) => void
  openWsFile: (path: string) => void
  deleteWsFile: (path: string) => void
}) {
  const prefix = p.store.data_path + '/'
  const rootFiles = p.store.data_files.filter(f => !f.path.slice(prefix.length).includes('/'))
  const folders = new Map<string, WsFile[]>()
  for (const f of p.store.data_files) {
    const rel = f.path.slice(prefix.length)
    const idx = rel.lastIndexOf('/')
    if (idx === -1) continue
    const folder = rel.slice(0, idx)
    if (!folders.has(folder)) folders.set(folder, [])
    folders.get(folder)!.push(f)
  }
  return (
    <>
      {rootFiles.map(f => (
        <WsFileItem key={f.path} file={f} selected={p.wsSelectedFile === f.path} onSelect={p.openWsFile} onDelete={p.deleteWsFile} />
      ))}
      {[...folders.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([folder, files]) => {
        const folderKey = `data:${p.store.slug}:${folder}`
        const open = p.wsExpandedStores.has(folderKey)
        return (
          <div key={folderKey}>
            <button
              onClick={() => p.toggleStoreExpanded(folderKey)}
              className="w-full text-left px-3 py-1 flex items-center gap-1.5 hover:bg-gray-50 text-xs text-gray-500"
            >
              <span className={`text-[9px] text-gray-400 transition-transform ${open ? 'rotate-90' : ''}`}>&#9654;</span>
              <span className="mr-0.5 opacity-60">&#128193;</span>
              <span className="truncate">{folder}/</span>
              <span className="text-[10px] text-gray-400 ml-auto flex-shrink-0">{files.length}</span>
            </button>
            {open && (
              <div className="ml-3">
                {files.map(f => (
                  <WsFileItem key={f.path} file={f} selected={p.wsSelectedFile === f.path} onSelect={p.openWsFile} onDelete={p.deleteWsFile} />
                ))}
              </div>
            )}
          </div>
        )
      })}
    </>
  )
}

/** One group per store, straight from the API: knowledge files plus
 *  (when present) the store's run data under a thin 运行数据 divider.
 *  The backend joins both trees by slug — no combining here. */
export function StoreFilesSection(p: StoreSectionsProps) {
  const { t } = useTranslation()
  const profiles = p.wsStructured?.store_profiles ?? []
  return (
    <div>
      <div className="px-3 pt-2 pb-1">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{t('workspace.files')}</p>
      </div>
      <p className="px-3 pb-2 text-[10px] text-gray-400 leading-tight">{t('workspace.storeFilesHint')}</p>
      {profiles.map(store => (
        <div key={store.slug} className="border-b border-gray-50">
          <button
            onClick={() => p.toggleStoreExpanded(store.slug)}
            className="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-gray-50 text-sm"
          >
            <span className={`text-[10px] text-gray-400 transition-transform ${p.wsExpandedStores.has(store.slug) ? 'rotate-90' : ''}`}>&#9654;</span>
            <span className="font-medium text-gray-700 text-xs">{store.slug}</span>
            {store.has_content && <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 flex-shrink-0" title={t('workspace.hasContent')} />}
            <span className="text-[10px] text-gray-400 ml-auto">{store.file_count + store.data_file_count}</span>
          </button>
          {p.wsExpandedStores.has(store.slug) && (
            <div className="ml-3">
              <div className="flex items-center px-3 py-0.5">
                <button
                  onClick={() => p.setWsNewFileSection(p.wsNewFileSection === store.path ? null : store.path)}
                  className="text-[10px] text-gray-400 hover:text-indigo-600"
                >+ {t('workspace.newFile')}</button>
              </div>
              {p.wsNewFileSection === store.path && (
                <div className="px-3 pb-1 flex gap-1">
                  <input
                    value={p.wsNewFileName}
                    onChange={e => p.setWsNewFileName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') p.createWsFile(store.path, p.wsNewFileName); if (e.key === 'Escape') p.setWsNewFileSection(null) }}
                    placeholder="filename.md"
                    className="flex-1 px-2 py-0.5 border border-gray-300 rounded text-xs"
                    autoFocus
                  />
                </div>
              )}
              {store.files.map(f => (
                <WsFileItem key={f.path} file={f} selected={p.wsSelectedFile === f.path} onSelect={p.openWsFile} onDelete={p.deleteWsFile} />
              ))}
              {store.files.length === 0 && store.data_files.length === 0 && (
                <div className="px-4 py-1 text-[10px] text-gray-400 italic">{t('common.noData')}</div>
              )}
              {store.data_files.length > 0 && (
                <>
                  <div className="px-3 pt-1.5 pb-0.5 flex items-center gap-1.5" title={t('workspace.storeDataHint')}>
                    <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">{t('workspace.storeData')}</span>
                    <span className="flex-1 border-t border-gray-100" />
                  </div>
                  <StoreDataFolders
                    store={store}
                    wsSelectedFile={p.wsSelectedFile}
                    wsExpandedStores={p.wsExpandedStores}
                    toggleStoreExpanded={p.toggleStoreExpanded}
                    openWsFile={p.openWsFile}
                    deleteWsFile={p.deleteWsFile}
                  />
                </>
              )}
            </div>
          )}
        </div>
      ))}
      {p.wsStructured && profiles.length === 0 && (
        <div className="px-4 py-2 text-xs text-gray-400 italic">{t('workspace.noStoresInWorkspace')}</div>
      )}
    </div>
  )
}

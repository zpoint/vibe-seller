import { useTranslation } from 'react-i18next'
import type { WsStructured } from '../types'

interface WorkspaceViewProps {
  wsSelectedFile: string | null
  wsEditorContent: string
  wsFileContent: string
  wsEditorDirty: boolean
  wsSaving: boolean
  wsShowHistory: boolean
  wsFileHistory: Array<{ sha: string; message: string; date: string; author: string }>
  wsPreviewCommit: string | null
  wsPreviewContent: string
  wsStructured: WsStructured | null
  setWsEditorContent: (v: string) => void
  setWsEditorDirty: (v: boolean) => void
  setWsShowHistory: (v: boolean) => void
  setWsPreviewCommit: (v: string | null) => void
  loadFileHistory: (path: string) => void
  previewVersion: (path: string, sha: string) => void
  resetFileToVersion: (path: string, sha: string) => void
  deleteWsFile: (path: string) => void
  saveWsFile: () => void
}

export function WorkspaceView({
  wsSelectedFile,
  wsEditorContent,
  wsFileContent,
  wsEditorDirty,
  wsSaving,
  wsShowHistory,
  wsFileHistory,
  wsPreviewCommit,
  wsPreviewContent,
  wsStructured,
  setWsEditorContent,
  setWsEditorDirty,
  setWsShowHistory,
  setWsPreviewCommit,
  loadFileHistory,
  previewVersion,
  resetFileToVersion,
  deleteWsFile,
  saveWsFile,
}: WorkspaceViewProps) {
  const { t } = useTranslation()
  const isProjectKnowledge = wsSelectedFile?.startsWith('knowledge/project/') ?? false
  const isBuiltinSkill = wsStructured?.skills.some(s => {
    if (s.source !== 'builtin') return false
    const prefix = s.path.endsWith('/') ? s.path : s.path + '/'
    return wsSelectedFile?.startsWith(prefix) ?? false
  }) ?? false
  const isImportedSkill = wsStructured?.skills.some(s => {
    if (s.source !== 'imported') return false
    const prefix = s.path.endsWith('/') ? s.path : s.path + '/'
    return wsSelectedFile?.startsWith(prefix) ?? false
  }) ?? false
  const isReadOnly = isProjectKnowledge || isBuiltinSkill

  if (!wsSelectedFile) {
    return (
      <div className="flex-1 flex flex-col bg-gray-50">
        <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2">
          <div className="text-4xl opacity-30">&#128218;</div>
          <p className="text-sm">{t('workspace.emptyState')}</p>
          <p className="text-xs text-gray-300">{t('workspace.knowledgeHint')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col bg-gray-50">
      <div className="px-4 py-3 bg-white border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <div className="min-w-0">
            <h2 className="font-semibold text-sm truncate">{wsSelectedFile.split('/').pop()}</h2>
            <p className="text-xs text-gray-400 truncate">{wsSelectedFile}</p>
          </div>
          {wsEditorDirty && <span className="text-xs text-amber-500 bg-amber-50 px-2 py-0.5 rounded-full flex-shrink-0">{t('workspace.readOnly')}</span>}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          <button
            onClick={() => { if (!wsShowHistory) { loadFileHistory(wsSelectedFile); setWsShowHistory(true); setWsPreviewCommit(null) } else { setWsShowHistory(false); setWsPreviewCommit(null) } }}
            className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${wsShowHistory ? 'bg-gray-200 text-gray-700' : 'text-gray-500 hover:bg-gray-100'}`}
          >
            {t('workspace.history')}
          </button>
          {!isReadOnly && (
            <>
              <button
                onClick={() => deleteWsFile(wsSelectedFile)}
                className="px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              >
                {t('common.delete')}
              </button>
              <button
                onClick={saveWsFile}
                disabled={!wsEditorDirty || wsSaving}
                className="px-4 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:bg-blue-700 transition-colors"
              >
                {wsSaving ? t('common.loading') : t('common.save')}
              </button>
            </>
          )}
        </div>
      </div>
      {isBuiltinSkill && (
        <div className="px-4 py-2 bg-blue-50 border-b border-blue-200">
          <p className="text-xs text-blue-700">{t('workspace.builtinSkillHint')}</p>
        </div>
      )}
      {isImportedSkill && (
        <div className="px-4 py-2 bg-purple-50 border-b border-purple-200 text-xs text-purple-700">
          {t('workspace.importedSkillHint')}
        </div>
      )}
      {wsSelectedFile?.startsWith('knowledge/project/') && (
        <div className="px-4 py-2 bg-gray-50 border-b border-gray-200">
          <p className="text-xs text-gray-500">{t('workspace.projectKnowledgeReadOnly')}</p>
        </div>
      )}
      {wsSelectedFile?.startsWith('knowledge/') && !wsSelectedFile?.startsWith('knowledge/project/') && (
        <div className="px-4 py-2 bg-green-50 border-b border-green-200">
          <p className="text-xs text-green-700">{t('workspace.localKnowledgeEditHint')}</p>
        </div>
      )}
      {wsShowHistory ? (
        <div className="flex-1 flex overflow-hidden">
          {/* History sidebar */}
          <div className="w-72 border-r border-gray-200 overflow-y-auto bg-white">
            {wsFileHistory.length === 0 ? (
              <p className="p-4 text-xs text-gray-400 italic">{t('workspace.noHistory')}</p>
            ) : (
              wsFileHistory.map((c, i) => (
                <div
                  key={c.sha}
                  className={`px-3 py-2 border-b border-gray-100 cursor-pointer hover:bg-blue-50 transition-colors ${wsPreviewCommit === c.sha ? 'bg-blue-50 border-l-2 border-l-blue-500' : ''}`}
                  onClick={() => previewVersion(wsSelectedFile!, c.sha)}
                >
                  <div className="flex items-center justify-between">
                    <code className="text-[10px] text-blue-600 font-mono">{c.sha}</code>
                    {i === 0 && <span className="text-[9px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded">{t('workspace.current')}</span>}
                  </div>
                  <p className="text-xs text-gray-700 mt-0.5 line-clamp-2">{c.message}</p>
                  <p className="text-[10px] text-gray-400 mt-0.5">{new Date(c.date).toLocaleString()}</p>
                </div>
              ))
            )}
          </div>
          {/* Preview pane */}
          <div className="flex-1 flex flex-col">
            {wsPreviewCommit ? (
              <>
                <div className="px-4 py-2 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
                  <span className="text-xs text-gray-500">{t('workspace.preview')}: <code className="font-mono text-blue-600">{wsPreviewCommit}</code></span>
                  {wsFileHistory[0]?.sha !== wsPreviewCommit && (
                    <button
                      onClick={() => resetFileToVersion(wsSelectedFile!, wsPreviewCommit!)}
                      className="px-3 py-1 text-xs font-medium bg-amber-500 text-white rounded-lg hover:bg-amber-600 transition-colors"
                    >
                      {t('workspace.resetToVersion')}
                    </button>
                  )}
                </div>
                <textarea
                  value={wsPreviewContent}
                  readOnly
                  className="flex-1 p-4 font-mono text-sm bg-gray-50 border-0 resize-none focus:outline-none leading-relaxed opacity-80"
                  spellCheck={false}
                />
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
                Select a commit to preview
              </div>
            )}
          </div>
        </div>
      ) : (
        <textarea
          value={wsEditorContent}
          onChange={e => { if (isReadOnly) return; setWsEditorContent(e.target.value); setWsEditorDirty(e.target.value !== wsFileContent) }}
          onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); if (!isReadOnly) saveWsFile() } }}
          className={`flex-1 p-4 font-mono text-sm border-0 resize-none focus:outline-none leading-relaxed ${isReadOnly ? 'bg-gray-50 text-gray-600 cursor-default' : 'bg-white'}`}
          spellCheck={false}
          readOnly={isReadOnly}
        />
      )}
    </div>
  )
}

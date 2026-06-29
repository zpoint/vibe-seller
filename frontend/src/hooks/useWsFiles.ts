import { api } from '../api'
import { fileKind, isRawKind } from '../lib/fileKind'

interface WsFileDeps {
  t: (key: string, opts?: Record<string, unknown>) => string
  wsSelectedFile: string | null
  wsEditorContent: string
  wsSaving: boolean
  setWsSelectedFile: (v: string | null) => void
  setWsFileContent: (v: string) => void
  setWsEditorContent: (v: string) => void
  setWsEditorDirty: (v: boolean) => void
  setWsShowHistory: (v: boolean) => void
  setWsPreviewCommit: (v: string | null) => void
  setWsPreviewContent: (v: string) => void
  setWsSaving: (v: boolean) => void
  setWsSyncing: (v: boolean) => void
  setWsSyncMeta: (v: Record<string, unknown> | null) => void
  setWsSkillsSyncing: (v: boolean) => void
  setWsFileHistory: (v: Array<{ sha: string; message: string; date: string; author: string }>) => void
  setWsNewFileName: (v: string) => void
  setWsNewFileSection: (v: string | null) => void
  loadWsStructured: () => Promise<void>
}

/** Workspace file CRUD/history/sync handlers (state stays in App). */
export function useWsFiles(d: WsFileDeps) {
  const clearSelection = (path: string | null, content = '') => {
    d.setWsSelectedFile(path); d.setWsFileContent(content); d.setWsEditorContent(content); d.setWsEditorDirty(false); d.setWsShowHistory(false); d.setWsPreviewCommit(null)
  }
  const openWsFile = async (path: string) => {
    /* raw kinds (xlsx/pdf/image/zip): viewers fetch /file/raw themselves */
    if (isRawKind(fileKind(path))) { clearSelection(path); return }
    try {
      const data = await api.get(`/api/workspace/file?path=${encodeURIComponent(path)}`)
      clearSelection(path, data.content)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      clearSelection(path)
      alert(`Failed to load file: ${msg}`)
    }
  }
  const saveWsFile = async () => { if (!d.wsSelectedFile || d.wsSaving) return; d.setWsSaving(true); try { await api.put(`/api/workspace/file?path=${encodeURIComponent(d.wsSelectedFile)}`, { content: d.wsEditorContent }); d.setWsFileContent(d.wsEditorContent); d.setWsEditorDirty(false); await d.loadWsStructured() } finally { d.setWsSaving(false) } }
  const syncProjectKnowledge = async () => { d.setWsSyncing(true); try { await api.post('/api/workspace/knowledge/sync'); await d.loadWsStructured(); try { d.setWsSyncMeta(await api.get('/api/workspace/knowledge/sync-meta')) } catch { /* ignore */ } } catch { /* ignore */ }; d.setWsSyncing(false) }
  const syncBuiltinSkills = async () => { d.setWsSkillsSyncing(true); try { await api.post('/api/workspace/skills/sync'); await d.loadWsStructured() } catch { /* ignore */ }; d.setWsSkillsSyncing(false) }
  const loadFileHistory = async (path: string) => { try { const data = await api.get(`/api/workspace/file/history?path=${encodeURIComponent(path)}`) as { commits: Array<{sha: string, message: string, date: string, author: string}> }; d.setWsFileHistory(data.commits || []) } catch { d.setWsFileHistory([]) } }
  const previewVersion = async (path: string, sha: string) => { try { const data = await api.get(`/api/workspace/file/version?path=${encodeURIComponent(path)}&commit=${encodeURIComponent(sha)}`) as { content: string }; d.setWsPreviewCommit(sha); d.setWsPreviewContent(data.content) } catch { /* ignore */ } }
  const resetFileToVersion = async (path: string, sha: string) => { if (!confirm(d.t('workspace.resetConfirm', { sha }))) return; try { await api.post('/api/workspace/file/reset', { path, commit: sha }); d.setWsPreviewCommit(null); d.setWsPreviewContent(''); await openWsFile(path); await loadFileHistory(path) } catch { /* ignore */ } }
  const createWsFile = async (section: string, fileName: string) => { if (!fileName.trim()) return; const name = fileName.trim().endsWith('.md') ? fileName.trim() : fileName.trim() + '.md'; const path = section === 'knowledge' ? `knowledge/${name}` : `${section}/${name}`; await api.put(`/api/workspace/file?path=${encodeURIComponent(path)}`, { content: `# ${name.replace('.md', '')}\n\n` }); await d.loadWsStructured(); await openWsFile(path); d.setWsNewFileName(''); d.setWsNewFileSection(null) }
  const deleteWsFile = async (path: string) => { await api.del(`/api/workspace/file?path=${encodeURIComponent(path)}`); if (d.wsSelectedFile === path) { d.setWsSelectedFile(null); d.setWsFileContent(''); d.setWsEditorContent(''); d.setWsEditorDirty(false) }; await d.loadWsStructured() }

  return { openWsFile, saveWsFile, syncProjectKnowledge, syncBuiltinSkills, loadFileHistory, previewVersion, resetFileToVersion, createWsFile, deleteWsFile }
}

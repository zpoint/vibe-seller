import { useRef, useCallback, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { PendingFile, Store } from '../types'
import { usePasteFiles } from '../hooks/usePasteFiles'
import { formatUploadLimit, getMaxUploadSize } from '../uploadLimits'
import { uuid } from '../uuid'
import { fileBadge, isPreviewImage } from '../lib/attachmentKind'

const EXT_BY_TYPE: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'application/pdf': 'pdf',
}

/** Give a nameless clipboard file a real name.
 *
 *  The upload sends `PendingFile.file`, and the server derives the
 *  stored extension from `File.name` alone
 *  (`app/routers/attachments.py`) — a blank name lands in the agent's
 *  cwd as `uploads/upload.bin`, so the screenshot the user pasted no
 *  longer reads as an image. A display-only label cannot fix that: it
 *  never reaches the wire. Rename the File itself and let the label
 *  derive from it, so what is shown and what is uploaded cannot drift
 *  apart. ASCII on purpose — the server strips anything else out of
 *  the stem. */
function withFilename(file: File): File {
  if (file.name) return file
  return new File([file], `pasted.${EXT_BY_TYPE[file.type] ?? 'bin'}`, {
    type: file.type,
    lastModified: file.lastModified,
  })
}

/** True when a drag carries files. `dataTransfer.files` is empty
 *  during dragover for security reasons, so `types` is the only
 *  signal available that early. */
function dragHasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes('Files')
}

interface CreateTaskModalProps {
  showAllTasks: boolean
  storeName: string | undefined
  selectedStore: Store | null
  onClose: () => void
  /** One prompt box: `title` is always '' and the server derives it
   *  from `description` (app/task_title.py). */
  onSubmit: (title: string, description: string, files: PendingFile[], platform?: string, country?: string) => Promise<void>
}

export function CreateTaskModal({ showAllTasks, storeName, selectedStore, onClose, onSubmit }: CreateTaskModalProps) {
  const { t } = useTranslation()
  const [modalPrompt, setModalPrompt] = useState('')
  const [modalFiles, setModalFiles] = useState<PendingFile[]>([])
  const [modalCreating, setModalCreating] = useState(false)
  const [selectedPlatform, setSelectedPlatform] = useState('')
  const [selectedCountry, setSelectedCountry] = useState('')
  const [dragOver, setDragOver] = useState(false)
  /** Why the last batch of files did not all make it in. Silent
   *  skipping reads as "attachments are broken" — the user has no way
   *  to tell a rejected file from a dead drop zone. */
  const [rejected, setRejected] = useState<string[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addFiles = useCallback((files: FileList | File[]) => {
    const newPending: PendingFile[] = []
    const skipped: string[] = []
    for (const original of Array.from(files)) {
      // A pasted screenshot can arrive nameless; say something about
      // it rather than opening the complaint with an empty string.
      const label = original.name || t('tasks.attachmentClipboardItem')
      if (original.size > getMaxUploadSize()) {
        skipped.push(t('tasks.attachmentTooLarge', {
          name: label,
          size: formatUploadLimit(),
        }))
        continue
      }
      // Any type is accepted. Name it before staging: `file.name` is
      // what gets uploaded.
      const file = withFilename(original)
      newPending.push({
        id: uuid(),
        file,
        // Only raster images get a thumbnail; the rest show a file chip.
        preview: isPreviewImage(file.name, file.type) ? URL.createObjectURL(file) : '',
        name: file.name,
      })
    }
    setModalFiles(prev => [...prev, ...newPending])
    setRejected(skipped)
  }, [t])

  // Ctrl/Cmd+V anywhere in the dialog — including right after clicking
  // the drop zone, which leaves focus on `document.body` and so never
  // reaches a React `onPaste`. See `usePasteFiles`.
  usePasteFiles(addFiles)

  const removeFile = (id: string) => {
    setModalFiles(prev => {
      const f = prev.find(p => p.id === id)
      if (f?.preview) URL.revokeObjectURL(f.preview)
      return prev.filter(p => p.id !== id)
    })
  }

  const handleDragOver = (e: DragEvent) => {
    if (!dragHasFiles(e)) return
    e.preventDefault()
    setDragOver(true)
  }
  const handleDragLeave = (e: DragEvent) => { e.preventDefault(); setDragOver(false) }
  const handleDrop = (e: DragEvent) => {
    setDragOver(false)
    if (!e.dataTransfer.files.length) return
    e.preventDefault()
    // The dialog-wide guard below would otherwise attach it twice.
    e.stopPropagation()
    addFiles(e.dataTransfer.files)
  }

  /** A file dropped anywhere else over the open dialog. Without this
   *  the browser navigates the tab to the dropped file and the
   *  half-typed task is gone, so a near-miss attaches instead.
   *
   *  Files only. Cancelling every drop would also cancel dragging
   *  selected text into the title or description, which the browser
   *  handles natively and which this dialog has no business eating. */
  const handleDialogDrop = (e: DragEvent) => {
    if (!e.dataTransfer.files.length) return
    e.preventDefault()
    addFiles(e.dataTransfer.files)
  }

  const handleClose = () => {
    modalFiles.forEach(f => { if (f.preview) URL.revokeObjectURL(f.preview) })
    onClose()
  }

  const handleSubmit = async () => {
    if (!modalPrompt.trim() || modalCreating) return
    setModalCreating(true)
    try {
      await onSubmit('', modalPrompt.trim(), modalFiles, selectedPlatform || undefined, selectedCountry || undefined)
      handleClose()
    } finally {
      setModalCreating(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) handleClose() }}
      onDragOver={(e) => { if (dragHasFiles(e)) e.preventDefault() }}
      onDrop={handleDialogDrop}
    >
      <div className="bg-white rounded-t-2xl sm:rounded-xl shadow-2xl w-full sm:max-w-lg sm:mx-4 max-h-[92vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="px-6 py-4 border-b border-gray-200">
          <h3 className="text-lg font-semibold">
            {showAllTasks ? t('tasks.newTask') : t('tasks.newTaskFor', { storeName })}
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">
            {t('tasks.newTaskSubtitle')}
          </p>
        </div>
        <div className="px-6 py-4 space-y-4">
          <div>
            <label htmlFor="task-prompt" className="sr-only">{t('tasks.promptLabel')}</label>
            <textarea
              id="task-prompt"
              value={modalPrompt}
              onChange={e => setModalPrompt(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Escape') handleClose()
                // Enter is a newline (prompts are multi-line);
                // Ctrl/Cmd+Enter submits, as in chat-style agents.
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  void handleSubmit()
                }
              }}
              placeholder={showAllTasks ? t('tasks.promptPlaceholderAll') : t('tasks.promptPlaceholderStore')}
              rows={6}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent resize-y min-h-[8rem]"
              autoFocus
            />
            <p className="text-xs text-gray-400 mt-1">{t('tasks.promptHint')}</p>
          </div>
          {selectedStore && selectedStore.platform_countries && Object.keys(selectedStore.platform_countries).length > 0 && (
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('stores.platform')} <span className="text-gray-400 font-normal">({t('common.optional')})</span></label>
                <select
                  value={selectedPlatform}
                  onChange={e => { setSelectedPlatform(e.target.value); setSelectedCountry('') }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
                >
                  <option value="">{t('stores.anyPlatform')}</option>
                  {Object.keys(selectedStore.platform_countries).map(p => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
              {selectedPlatform && (selectedStore.platform_countries[selectedPlatform]?.length ?? 0) > 0 && (
                <div className="flex-1">
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('stores.country')} <span className="text-gray-400 font-normal">({t('common.optional')})</span></label>
                  <select
                    value={selectedCountry}
                    onChange={e => setSelectedCountry(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
                  >
                    <option value="">{t('stores.anyCountry')}</option>
                    {selectedStore.platform_countries[selectedPlatform].map(c => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('tasks.attachmentsLabel')} <span className="text-gray-400 font-normal">({t('common.optional')})</span></label>
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors ${
                dragOver ? 'border-indigo-500 bg-indigo-50' : 'border-gray-300 hover:border-gray-400'
              }`}
            >
              <p className="text-sm text-gray-500">{t('tasks.attachmentsHint')}</p>
              <p className="text-xs text-gray-400 mt-1">{t('tasks.attachmentsTypes', { size: formatUploadLimit() })}</p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={e => { if (e.target.files?.length) addFiles(e.target.files); e.target.value = '' }}
              />
            </div>
            {rejected.length > 0 && (
              <ul className="mt-2 space-y-0.5" data-testid="attachment-rejected">
                {rejected.map((msg, i) => (
                  <li key={i} className="text-xs text-red-600">{msg}</li>
                ))}
              </ul>
            )}
            {modalFiles.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-3">
                {modalFiles.map(pf => (
                  <div key={pf.id} className="relative group">
                    {pf.preview ? (
                      <img src={pf.preview} alt={pf.name} className="w-16 h-16 object-cover rounded-lg border border-gray-200" />
                    ) : (
                      <div data-testid="attachment-file-chip" className="w-16 h-16 flex items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
                        <span className="text-xs font-semibold text-gray-500">{fileBadge(pf.name)}</span>
                      </div>
                    )}
                    <button
                      onClick={(e) => { e.stopPropagation(); removeFile(pf.id) }}
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 text-white rounded-full text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      x
                    </button>
                    <div className="text-xs text-gray-400 mt-0.5 w-16 truncate text-center">{pf.name}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="px-6 py-4 border-t border-gray-200 flex justify-end gap-3">
          <button
            onClick={handleClose}
            className="px-4 py-2 text-sm text-gray-700 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={!modalPrompt.trim() || modalCreating}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {modalCreating ? t('common.loading') : showAllTasks ? t('common.create') : t('tasks.createAndRun')}
          </button>
        </div>
      </div>
    </div>
  )
}

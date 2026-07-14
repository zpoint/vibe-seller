import { useRef, useCallback, useState, type DragEvent, type ClipboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { PendingFile, Store } from '../types'
import { uuid } from '../uuid'

interface CreateTaskModalProps {
  showAllTasks: boolean
  storeName: string | undefined
  selectedStore: Store | null
  onClose: () => void
  onSubmit: (title: string, description: string, files: PendingFile[], platform?: string, country?: string) => Promise<void>
}

export function CreateTaskModal({ showAllTasks, storeName, selectedStore, onClose, onSubmit }: CreateTaskModalProps) {
  const { t } = useTranslation()
  const [modalTitle, setModalTitle] = useState('')
  const [modalDescription, setModalDescription] = useState('')
  const [modalFiles, setModalFiles] = useState<PendingFile[]>([])
  const [modalCreating, setModalCreating] = useState(false)
  const [selectedPlatform, setSelectedPlatform] = useState('')
  const [selectedCountry, setSelectedCountry] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const titleInputRef = useRef<HTMLInputElement>(null)

  const addFiles = useCallback((files: FileList | File[]) => {
    const allowed = ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/pdf']
    const maxSize = 10 * 1024 * 1024
    const newPending: PendingFile[] = []
    for (const file of Array.from(files)) {
      if (!allowed.includes(file.type)) continue
      if (file.size > maxSize) continue
      newPending.push({
        id: uuid(),
        file,
        preview: file.type.startsWith('image/') ? URL.createObjectURL(file) : '',
        name: file.name,
      })
    }
    setModalFiles(prev => [...prev, ...newPending])
  }, [])

  const removeFile = (id: string) => {
    setModalFiles(prev => {
      const f = prev.find(p => p.id === id)
      if (f) URL.revokeObjectURL(f.preview)
      return prev.filter(p => p.id !== id)
    })
  }

  const handleDragOver = (e: DragEvent) => { e.preventDefault(); setDragOver(true) }
  const handleDragLeave = (e: DragEvent) => { e.preventDefault(); setDragOver(false) }
  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files)
  }

  const handlePaste = (e: ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return
    const files: File[] = []
    for (const item of Array.from(items)) {
      if (item.kind === 'file') {
        const f = item.getAsFile()
        if (f) files.push(f)
      }
    }
    if (files.length) addFiles(files)
  }

  const handleClose = () => {
    modalFiles.forEach(f => URL.revokeObjectURL(f.preview))
    onClose()
  }

  const handleSubmit = async () => {
    if (!modalTitle.trim() || modalCreating) return
    setModalCreating(true)
    try {
      await onSubmit(modalTitle.trim(), modalDescription.trim(), modalFiles, selectedPlatform || undefined, selectedCountry || undefined)
      handleClose()
    } finally {
      setModalCreating(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) handleClose() }}
      onPaste={handlePaste}
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
            <label htmlFor="task-title" className="block text-sm font-medium text-gray-700 mb-1">{t('tasks.titleLabel')}</label>
            <input
              id="task-title"
              ref={titleInputRef}
              value={modalTitle}
              onChange={e => setModalTitle(e.target.value)}
              onKeyDown={e => { if (e.key === 'Escape') handleClose() }}
              placeholder={showAllTasks ? 'e.g. Process billing files' : 'e.g. Navigate to google.com'}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              autoFocus
            />
          </div>
          <div>
            <label htmlFor="task-desc" className="block text-sm font-medium text-gray-700 mb-1">{t('common.description')} <span className="text-gray-400 font-normal">({t('common.optional')})</span></label>
            <textarea
              id="task-desc"
              value={modalDescription}
              onChange={e => setModalDescription(e.target.value)}
              onKeyDown={e => { if (e.key === 'Escape') handleClose() }}
              placeholder={t('tasks.descriptionPlaceholder')}
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent resize-none"
            />
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
            <label className="block text-sm font-medium text-gray-700 mb-1">Attachments <span className="text-gray-400 font-normal">(optional)</span></label>
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors ${
                dragOver ? 'border-indigo-500 bg-indigo-50' : 'border-gray-300 hover:border-gray-400'
              }`}
            >
              <p className="text-sm text-gray-500">Drop images here, paste, or click to browse</p>
              <p className="text-xs text-gray-400 mt-1">PNG, JPEG, GIF, WebP, PDF up to 10MB</p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/png,image/jpeg,image/gif,image/webp,application/pdf"
                className="hidden"
                onChange={e => { if (e.target.files?.length) addFiles(e.target.files); e.target.value = '' }}
              />
            </div>
            {modalFiles.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-3">
                {modalFiles.map(pf => (
                  <div key={pf.id} className="relative group">
                    {pf.preview ? (
                      <img src={pf.preview} alt={pf.name} className="w-16 h-16 object-cover rounded-lg border border-gray-200" />
                    ) : (
                      <div className="w-16 h-16 flex items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
                        <span className="text-xs text-gray-500">PDF</span>
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
            disabled={!modalTitle.trim() || modalCreating}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {modalCreating ? t('common.loading') : showAllTasks ? t('common.create') : 'Create & Run'}
          </button>
        </div>
      </div>
    </div>
  )
}

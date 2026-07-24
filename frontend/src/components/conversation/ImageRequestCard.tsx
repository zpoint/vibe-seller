import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ImageModelOption } from '../../types'
import { ModelCascadeSelect } from './ModelCascadeSelect'

interface AddedRef { path: string; url: string }

interface ImageRequestCardProps {
  taskId: string
  requestId: string
  prompt: string
  model: string
  models: ImageModelOption[]
  referenceImages: string[]
  kind?: string
  resolved?: boolean
  expired?: boolean
  generating?: boolean
  onDecision: (
    requestId: string,
    action: 'confirm' | 'cancel',
    prompt: string,
    model: string,
    addedReferences: string[],
  ) => void
}

/** Preview URL for a reference: remote URLs go through the backend
 *  proxy (supplier CDNs like 1688 fail to load cross-origin in the
 *  browser); workspace paths are served by the task-files endpoint. */
function previewUrl(taskId: string, ref: string): string {
  if (ref.startsWith('http')) {
    return `/api/vision/ref-proxy?url=${encodeURIComponent(ref)}`
  }
  return `/api/tasks/${taskId}/files/${ref}`
}

/** Inline confirm card: the agent proposed an image; the user reviews
 *  and edits the prompt/model and can add their own reference image,
 *  before anything is generated. */
export function ImageRequestCard({
  taskId,
  requestId,
  prompt,
  model,
  models,
  referenceImages,
  kind,
  resolved,
  expired,
  generating,
  onDecision,
}: ImageRequestCardProps) {
  const { t } = useTranslation()
  const [editedPrompt, setEditedPrompt] = useState(prompt)
  const [editedModel, setEditedModel] = useState(model)
  const [added, setAdded] = useState<AddedRef[]>([])
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const decide = (action: 'confirm' | 'cancel') => {
    setBusy(true)
    onDecision(
      requestId, action, editedPrompt, editedModel,
      added.map(a => a.path),
    )
  }

  const uploadFiles = async (files: FileList | File[]) => {
    setUploading(true)
    try {
      for (const f of Array.from(files)) {
        if (!f.type.startsWith('image/')) continue
        const fd = new FormData()
        fd.append('file', f)
        const resp = await fetch(
          `/api/tasks/${taskId}/image/upload-reference`,
          { method: 'POST', body: fd, credentials: 'include' },
        )
        if (resp.ok) {
          const data = await resp.json()
          setAdded(prev => [...prev, { path: data.path, url: data.url }])
        }
      }
    } finally {
      setUploading(false)
    }
  }

  // The selectable catalog (provider grouping, price formatting, and the
  // two-level cascade live in <ModelCascadeSelect/>). Fall back to a
  // synthetic single option if the catalog didn't reach us.
  const catalog: ImageModelOption[] = models.length
    ? models
    : [{ id: model, provider: '', label: model, usd: 0, cny: 0 }]

  const thumbClass =
    'h-28 w-28 object-contain rounded border border-gray-200 bg-white'

  return (
    <div
      data-testid="image-request-card"
      className="mb-4 rounded-xl border border-indigo-200 bg-gradient-to-b from-indigo-50 to-white overflow-hidden shadow-sm"
    >
      <div className="px-4 py-3 border-b border-indigo-100 flex items-center gap-2">
        <span className="text-base font-semibold text-indigo-800">
          {t('vision.confirmTitle')}
        </span>
        {kind && (
          <span className="px-1.5 py-0.5 bg-indigo-200/80 text-indigo-800 rounded text-[10px] font-semibold uppercase tracking-wide">
            {kind}
          </span>
        )}
      </div>
      <div className="px-4 py-4 space-y-4">
        <div className="space-y-1">
          <label className="text-sm font-medium text-gray-700">
            {t('vision.promptLabel')}
          </label>
          <textarea
            data-testid="image-prompt-input"
            value={editedPrompt}
            onChange={e => setEditedPrompt(e.target.value)}
            disabled={resolved || busy}
            className="w-full min-h-[160px] px-3 py-2 text-sm leading-relaxed border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent resize-y disabled:bg-gray-50 disabled:text-gray-400"
          />
        </div>
        <div className="space-y-1">
          <label className="text-sm font-medium text-gray-700">
            {t('vision.modelLabel')}
          </label>
          <ModelCascadeSelect
            models={catalog}
            value={editedModel}
            disabled={resolved || busy}
            onChange={setEditedModel}
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-700">
            {t('vision.referencesLabel')} ({referenceImages.length + added.length})
          </label>
          <div className="flex flex-wrap gap-2">
            {referenceImages.map((ref, i) => (
              <a
                key={`r-${i}`}
                href={ref.startsWith('http') ? ref : previewUrl(taskId, ref)}
                target="_blank"
                rel="noreferrer"
                title={ref}
              >
                <img src={previewUrl(taskId, ref)} alt="" className={thumbClass} />
              </a>
            ))}
            {added.map((a, i) => (
              <a key={`a-${i}`} href={a.url} target="_blank" rel="noreferrer" title={a.path}>
                <img src={a.url} alt="" className={`${thumbClass} ring-2 ring-indigo-300`} />
              </a>
            ))}
          </div>

          {!resolved && (
            <div
              data-testid="image-ref-dropzone"
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={e => {
                e.preventDefault(); setDragOver(false)
                if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files)
              }}
              onClick={() => fileInput.current?.click()}
              className={`mt-1 flex items-center justify-center gap-2 px-3 py-4 text-xs rounded-lg border-2 border-dashed cursor-pointer transition-colors ${
                dragOver
                  ? 'border-indigo-400 bg-indigo-50 text-indigo-700'
                  : 'border-gray-300 text-gray-500 hover:border-indigo-300 hover:text-indigo-600'
              }`}
            >
              <input
                ref={fileInput}
                data-testid="image-ref-file-input"
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={e => { if (e.target.files?.length) uploadFiles(e.target.files) }}
              />
              {uploading ? t('vision.uploading') : t('vision.dropHint')}
            </div>
          )}
        </div>
      </div>
      {!resolved && (
        <div className="px-4 py-3 bg-indigo-50/50 border-t border-indigo-100 flex items-center gap-2">
          <button
            data-testid="image-confirm-btn"
            onClick={() => decide('confirm')}
            disabled={busy || uploading || !editedPrompt.trim()}
            className="px-5 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-sm"
          >
            {t('vision.confirmGenerate')}
          </button>
          <button
            data-testid="image-cancel-btn"
            onClick={() => decide('cancel')}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-40 transition-colors"
          >
            {t('vision.cancel')}
          </button>
        </div>
      )}
      {resolved && (
        generating ? (
          <div
            data-testid="image-card-generating"
            className="px-4 py-3 bg-indigo-50/60 border-t border-indigo-100 flex items-center gap-2 text-sm text-indigo-700"
          >
            <span className="block w-4 h-4 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
            {t('vision.generating')}
          </div>
        ) : (
          <div
            data-testid="image-card-footer"
            className="px-4 py-2 bg-gray-50 border-t border-gray-100 text-xs text-gray-400"
          >
            {expired ? t('vision.expired') : t('vision.handled')}
          </div>
        )
      )}
    </div>
  )
}

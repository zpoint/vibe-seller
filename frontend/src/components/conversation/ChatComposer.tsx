import { useTranslation } from 'react-i18next'
import type { StagedAttachment } from '../../types'
import { ChatAttachButton } from './ChatAttachButton'
import { AttachmentChips } from './AttachmentChips'

interface ChatComposerProps {
  fileInputRef: React.RefObject<HTMLInputElement | null>
  uploading: boolean
  uploadFiles: (files: FileList | File[]) => void
  attachments: StagedAttachment[]
  onRemoveAttachment: (id: string) => void
  inputRef: React.RefObject<HTMLTextAreaElement | null>
  input: string
  setInput: (v: string) => void
  /** True when there is prose OR at least one attachment to send. */
  hasContent: boolean
  canSend: boolean
  isActive: boolean
  onSend: () => void
  onStop: () => void
  placeholder: string
}

/** The task chat send bar: removable attachment chips, attach button,
 *  auto-growing textarea (drag/paste upload), and the Send/Stop button.
 *  Attachments are staged and shown as thumbnails — never a raw path —
 *  and only reach the agent when the user sends. */
export function ChatComposer({
  fileInputRef, uploading, uploadFiles, attachments, onRemoveAttachment,
  inputRef, input, setInput, hasContent, canSend, isActive, onSend, onStop,
  placeholder,
}: ChatComposerProps) {
  const { t } = useTranslation()
  return (
    <>
      {/* Staged attachment chips (thumbnails, not paths) */}
      <AttachmentChips attachments={attachments} onRemove={onRemoveAttachment} />

      {/* While the agent is working, a follow-up is queued and delivered
       *  when the current step finishes (it can't interrupt a running
       *  tool such as image generation) — set that expectation. */}
      {isActive && hasContent && (
        <div className="text-[11px] text-gray-400" data-testid="chat-queued-hint">
          {t('tasks.queuedWhileWorking')}
        </div>
      )}

      <div className="flex gap-2 items-end">
        <ChatAttachButton
          fileInputRef={fileInputRef}
          uploading={uploading}
          disabled={!canSend && !isActive}
          onFiles={uploadFiles}
        />
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          rows={1}
          value={input}
          onChange={e => setInput(e.target.value)}
          onDragOver={e => e.preventDefault()}
          onDrop={e => {
            e.preventDefault()
            if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files)
          }}
          onPaste={e => {
            const imgs = Array.from(e.clipboardData?.items || [])
              .filter(i => i.kind === 'file' && i.type.startsWith('image/'))
              .map(i => i.getAsFile())
              .filter((f): f is File => !!f)
            if (imgs.length) { e.preventDefault(); uploadFiles(imgs) }
          }}
          onKeyDown={e => {
            // Bail on IME composition keystrokes. Chinese / Japanese /
            // Korean IMEs dispatch an Enter `keydown` to confirm a
            // composition BEFORE the real submit Enter ~50ms later —
            // without this guard both fire onSend, producing duplicate
            // user messages. keyCode===229 catches older Safari / Firefox
            // builds where isComposing is unreliable.
            if (e.nativeEvent.isComposing || e.keyCode === 229) return
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              if (hasContent) onSend()
            }
          }}
          placeholder={placeholder}
          disabled={!canSend && !isActive}
          className="flex-1 px-3 py-2 text-sm leading-5 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed resize-none overflow-y-auto"
        />
        {canSend && hasContent ? (
          <button
            onClick={onSend}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors"
          >
            {t('tasks.send')}
          </button>
        ) : isActive && !hasContent ? (
          <button
            onClick={onStop}
            className="px-3 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors"
          >
            {t('tasks.stopTask')}
          </button>
        ) : (
          <button
            disabled
            className="px-4 py-2 text-sm font-medium text-white bg-gray-300 rounded-lg cursor-not-allowed"
          >
            {t('tasks.send')}
          </button>
        )}
      </div>
    </>
  )
}

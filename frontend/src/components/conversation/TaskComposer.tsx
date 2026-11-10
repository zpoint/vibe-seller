import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { StagedAttachment } from '../../types'
import { useDraft, type DraftStore } from '../../lib/draftStore'
import { isRequestTimeout } from '../../lib/requestDeadline'
import { ChatComposer } from './ChatComposer'

interface TaskComposerProps {
  draft: DraftStore
  taskId: string
  fileInputRef: React.RefObject<HTMLInputElement | null>
  uploading: boolean
  uploadFiles: (files: FileList | File[]) => void
  attachments: StagedAttachment[]
  setAttachments: React.Dispatch<React.SetStateAction<StagedAttachment[]>>
  onRemoveAttachment: (id: string) => void
  inputRef: React.RefObject<HTMLTextAreaElement | null>
  canSend: boolean
  isActive: boolean
  awaitingUser?: boolean
  /** Sends the draft; rejects when the message was not accepted. */
  onSend: () => Promise<void> | void
  placeholder: string
}

/**
 * The task view's composer, bound to the draft store. It is the only
 * component that subscribes to the draft, so typing re-renders this
 * subtree and not the task view around it.
 *
 * A send shows that it is in flight and, if it fails (server down,
 * error, or no answer before the request deadline), says so and puts
 * the text and attachments back — a send never fails silently.
 * Mount it with `key={taskId}`: a send that settles after the user has
 * switched tasks then has nothing to restore into.
 */
export function TaskComposer({
  draft, taskId, attachments, setAttachments, inputRef, onSend, ...rest
}: TaskComposerProps) {
  const { t } = useTranslation()
  const input = useDraft(draft)
  // A message may be text-only, attachment-only, or both.
  const hasContent = input.trim().length > 0 || attachments.length > 0
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  // Auto-grow the textarea up to ~8 lines, then scroll.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 192)}px`
  }, [input, taskId, inputRef])

  const send = async () => {
    if (sending) return
    const text = draft.get()
    const staged = attachments
    setSendError(null)
    setSending(true)
    try {
      await onSend()
    } catch (e) {
      if (!mounted.current) return
      // Put back what was sent — unless the user has started a new one.
      if (!draft.get()) draft.set(text)
      setAttachments(prev => (prev.length ? prev : staged))
      setSendError(isRequestTimeout(e)
        ? t('chat.sendTimeout')
        : t('chat.sendFailed', { error: e instanceof Error ? e.message : String(e) }))
    } finally {
      if (mounted.current) setSending(false)
    }
  }

  return (
    <ChatComposer
      {...rest}
      attachments={attachments}
      inputRef={inputRef}
      input={input}
      setInput={draft.set}
      hasContent={hasContent}
      onSend={send}
      sending={sending}
      sendError={sendError}
    />
  )
}

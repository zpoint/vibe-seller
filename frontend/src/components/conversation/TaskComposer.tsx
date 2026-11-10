import { useEffect } from 'react'
import type { StagedAttachment } from '../../types'
import { useDraft, type DraftStore } from '../../lib/draftStore'
import { ChatComposer } from './ChatComposer'

interface TaskComposerProps {
  draft: DraftStore
  taskId: string
  fileInputRef: React.RefObject<HTMLInputElement | null>
  uploading: boolean
  uploadFiles: (files: FileList | File[]) => void
  attachments: StagedAttachment[]
  onRemoveAttachment: (id: string) => void
  inputRef: React.RefObject<HTMLTextAreaElement | null>
  canSend: boolean
  isActive: boolean
  awaitingUser?: boolean
  onSend: () => void
  placeholder: string
}

/**
 * The task view's composer, bound to the draft store. It is the only
 * component that subscribes to the draft, so typing re-renders this
 * subtree and not the task view around it.
 */
export function TaskComposer({ draft, taskId, attachments, inputRef, ...rest }: TaskComposerProps) {
  const input = useDraft(draft)
  // A message may be text-only, attachment-only, or both.
  const hasContent = input.trim().length > 0 || attachments.length > 0

  // Auto-grow the textarea up to ~8 lines, then scroll.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 192)}px`
  }, [input, taskId, inputRef])

  return (
    <ChatComposer
      {...rest}
      attachments={attachments}
      inputRef={inputRef}
      input={input}
      setInput={draft.set}
      hasContent={hasContent}
    />
  )
}

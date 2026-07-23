import { useRef, useState } from 'react'
import type { StagedAttachment } from '../types'

/** Chat attachments: upload files to STAGING (outside the agent's cwd)
 *  and hold them as removable chips. Nothing is written into the message
 *  text and nothing reaches the agent until the user hits Send — the
 *  server promotes staged files into the workspace only then. Drag, paste
 *  and click-upload in the send bar all land here.
 *
 *  Attachment state is owned by the caller (App) so the send handler can
 *  read the ids and clear them after a successful send. */
export function useChatUploads(
  taskId: string | null,
  setAttachments: React.Dispatch<React.SetStateAction<StagedAttachment[]>>,
) {
  const chatFileInputRef = useRef<HTMLInputElement>(null)
  const [chatUploading, setChatUploading] = useState(false)

  const uploadChatFiles = async (files: FileList | File[]) => {
    if (!taskId) return
    setChatUploading(true)
    try {
      for (const f of Array.from(files)) {
        const fd = new FormData()
        fd.append('file', f)
        const resp = await fetch(
          `/api/tasks/${taskId}/staged`,
          { method: 'POST', body: fd, credentials: 'include' },
        )
        if (resp.ok) {
          const d = await resp.json()
          setAttachments(prev => [...prev, {
            id: d.id,
            filename: d.filename,
            contentType: d.content_type,
            url: d.url,
          }])
        }
      }
    } finally {
      setChatUploading(false)
    }
  }

  /** Drop a staged attachment before sending (the chip's ✕). Removes the
   *  chip immediately; the server-side discard is best-effort. */
  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id))
    if (taskId) {
      fetch(`/api/tasks/${taskId}/staged/${id}`,
        { method: 'DELETE', credentials: 'include' }).catch(() => {})
    }
  }

  return { chatFileInputRef, chatUploading, uploadChatFiles, removeAttachment }
}

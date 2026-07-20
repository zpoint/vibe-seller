import { useRef, useState } from 'react'
import type { Task } from '../types'

/** Chat attachments: upload files into the task workspace and insert
 *  each returned absolute path into the message text, so the agent can
 *  Read the file directly (images become vision input). Drag, paste
 *  and click-upload in the send bar all land here. */
export function useChatUploads(
  selectedTask: Task | null,
  chatInput: string,
  setChatInput: (v: string) => void,
  chatInputRef: React.RefObject<HTMLTextAreaElement | null>,
  attachedLabel: string,
) {
  const chatFileInputRef = useRef<HTMLInputElement>(null)
  const [chatUploading, setChatUploading] = useState(false)

  const uploadChatFiles = async (files: FileList | File[]) => {
    if (!selectedTask) return
    setChatUploading(true)
    try {
      const paths: string[] = []
      for (const f of Array.from(files)) {
        const fd = new FormData()
        fd.append('file', f)
        const resp = await fetch(
          `/api/tasks/${selectedTask.id}/files/upload`,
          { method: 'POST', body: fd, credentials: 'include' },
        )
        if (resp.ok) {
          const data = await resp.json()
          paths.push(data.abs_path)
        }
      }
      if (paths.length) {
        const cur = chatInput.trim()
        const insert = paths.map(p => `${attachedLabel}: ${p}`).join('\n')
        setChatInput(cur ? `${cur}\n${insert}` : insert)
        chatInputRef.current?.focus()
      }
    } finally {
      setChatUploading(false)
    }
  }

  return { chatFileInputRef, chatUploading, uploadChatFiles }
}

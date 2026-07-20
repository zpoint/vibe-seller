import { useTranslation } from 'react-i18next'

interface ChatAttachButtonProps {
  fileInputRef: React.RefObject<HTMLInputElement | null>
  uploading: boolean
  disabled: boolean
  onFiles: (files: FileList) => void
}

/** Paperclip button + hidden file input for chat attachments. */
export function ChatAttachButton({
  fileInputRef,
  uploading,
  disabled,
  onFiles,
}: ChatAttachButtonProps) {
  const { t } = useTranslation()
  return (
    <>
      <input
        ref={fileInputRef}
        data-testid="chat-file-input"
        type="file"
        accept="image/*,.pdf"
        multiple
        className="hidden"
        onChange={e => {
          if (e.target.files?.length) onFiles(e.target.files)
          e.target.value = ''
        }}
      />
      <button
        data-testid="chat-attach-btn"
        onClick={() => fileInputRef.current?.click()}
        disabled={disabled || uploading}
        title={t('tasks.attachImage')}
        className="px-2.5 py-2 text-gray-400 hover:text-indigo-600 border border-gray-300 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {uploading ? (
          <span className="block w-4 h-4 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
        ) : (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
        )}
      </button>
    </>
  )
}

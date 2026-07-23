import type { StagedAttachment } from '../../types'

interface AttachmentChipsProps {
  attachments: StagedAttachment[]
  onRemove: (id: string) => void
}

const isPdf = (a: StagedAttachment) =>
  a.contentType === 'application/pdf' || /\.pdf$/i.test(a.filename)

/** Removable preview chips for staged chat attachments, shown above the
 *  send bar. Images render as thumbnails, PDFs as a labelled file chip —
 *  the raw path is never shown. */
export function AttachmentChips({ attachments, onRemove }: AttachmentChipsProps) {
  if (!attachments.length) return null
  return (
    <div className="flex flex-wrap gap-2" data-testid="chat-attachment-chips">
      {attachments.map(a => (
        <div
          key={a.id}
          data-testid="chat-attachment-chip"
          className="relative group flex items-center gap-2 pl-1 pr-2 py-1 bg-gray-50 border border-gray-200 rounded-lg"
        >
          {isPdf(a) ? (
            <span className="flex items-center justify-center w-9 h-9 rounded bg-red-50 text-red-500 text-[10px] font-semibold">
              PDF
            </span>
          ) : (
            <img
              src={a.url}
              alt={a.filename}
              className="w-9 h-9 rounded object-cover bg-gray-100"
            />
          )}
          <span className="max-w-[10rem] truncate text-xs text-gray-600" title={a.filename}>
            {a.filename}
          </span>
          <button
            type="button"
            data-testid="chat-attachment-remove"
            onClick={() => onRemove(a.id)}
            aria-label={`Remove ${a.filename}`}
            className="flex items-center justify-center w-5 h-5 rounded-full bg-gray-200 text-gray-600 hover:bg-gray-300 hover:text-gray-800 transition-colors"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
      ))}
    </div>
  )
}

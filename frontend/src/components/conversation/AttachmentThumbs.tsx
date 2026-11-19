import type { UserAttachment } from '../../lib/userContent'

/** Thumbnails (images) / chips (PDFs) for a user message's attachments. */
export function AttachmentThumbs({ images, className = '' }: { images: UserAttachment[]; className?: string }) {
  if (images.length === 0) return null
  return (
    <div className={`flex flex-wrap gap-2 ${className}`}>
      {images.map((im, i) => /\.pdf$/i.test(im.alt) || /\.pdf$/i.test(im.url) ? (
        <a key={i} href={im.url} target="_blank" rel="noreferrer"
          className="flex items-center gap-2 px-2 py-1.5 bg-white border border-gray-200 rounded-lg text-xs text-gray-600 hover:bg-gray-50">
          <span className="flex items-center justify-center w-6 h-6 rounded bg-red-50 text-red-500 text-[9px] font-semibold">PDF</span>
          <span className="max-w-[10rem] truncate">{im.alt || 'document.pdf'}</span>
        </a>
      ) : (
        <a key={i} href={im.url} target="_blank" rel="noreferrer">
          <img src={im.url} alt={im.alt}
            className="max-h-40 max-w-[12rem] rounded-lg border border-gray-200 object-cover" />
        </a>
      ))}
    </div>
  )
}

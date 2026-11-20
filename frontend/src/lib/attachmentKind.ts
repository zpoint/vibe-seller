/** Which attachments get a thumbnail, and how the rest are labelled.
 *
 *  Any file type may be attached; only raster images are previewed.
 *  SVG is deliberately NOT one of them — the server serves it as a
 *  download (app/uploads.py `serve_file`), so an <img> of it would be
 *  broken. Kept in step with the server's inline-serve list. */
const PREVIEW_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
const PREVIEW_EXT = /\.(png|jpe?g|gif|webp)$/i

/** True when the file should render as an image thumbnail. The MIME
 *  type wins when known; otherwise the name (or URL) decides. */
export function isPreviewImage(name: string, type?: string | null): boolean {
  if (type) return PREVIEW_TYPES.includes(type)
  return PREVIEW_EXT.test(name)
}

/** Short badge for a non-image chip: the extension, else "FILE". */
export function fileBadge(name: string): string {
  const m = /\.([a-z0-9]{1,5})$/i.exec(name)
  return m ? m[1].toUpperCase() : 'FILE'
}

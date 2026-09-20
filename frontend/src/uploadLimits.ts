/** The upload size cap, as the SERVER defines it.
 *
 *  The number lives in `app/uploads.py` and arrives via
 *  `GET /api/settings` (`max_upload_size`), which the app already
 *  fetches at boot. Nothing here should ever state the limit in prose:
 *  the dialog's hint text and its "too large" message both interpolate
 *  `formatUploadLimit()`, so raising the cap server-side updates the
 *  copy without touching the frontend.
 *
 *  This mattered because the limit used to be written down in three
 *  places — 10MB for create-task attachments, 15MB for chat staging,
 *  and 10MB hardcoded here — and they had already drifted apart, so
 *  the same file was refused by one box and accepted by the next.
 */

/** Pre-boot fallback, used only until `/api/settings` answers. The
 *  server is authoritative and re-checks every upload, so a stale
 *  value here costs at most one rejected request, never a wrong
 *  accept. */
const FALLBACK_MAX_UPLOAD_SIZE = 100 * 1024 * 1024

let maxUploadSize = FALLBACK_MAX_UPLOAD_SIZE

/** Called once at boot with the `/api/settings` payload. */
export function initUploadLimits(settings: Record<string, string>): void {
  const parsed = Number(settings.max_upload_size)
  if (Number.isFinite(parsed) && parsed > 0) {
    maxUploadSize = parsed
  }
}

export function getMaxUploadSize(): number {
  return maxUploadSize
}

/** `104857600` -> `'100MB'`. Mirrors `human_size` in app/uploads.py. */
export function formatUploadLimit(bytes: number = maxUploadSize): string {
  const mb = bytes / (1024 * 1024)
  return mb >= 1 ? `${Math.round(mb)}MB` : `${bytes}B`
}

import { useEffect, useRef } from 'react'

/** Accept clipboard files (Ctrl/Cmd+V) anywhere on the page while the
 *  calling component is mounted.
 *
 *  Why a document listener and not an `onPaste` prop on the dialog:
 *  a paste event is dispatched to the FOCUSED element, and React 18
 *  delegates from the root container (`#root` — see `main.tsx`). Click
 *  the dashed drop zone and focus lands on `document.body`, because a
 *  plain `<div>` is not focusable — so the event path is
 *  body -> html -> document and never crosses `#root`. No `onPaste`
 *  anywhere in the React tree runs, and the box that literally says
 *  "paste" swallows every Ctrl+V.
 *
 *  The affordance a dialog advertises must not depend on where focus
 *  happens to be, so the subscription lives where the event actually
 *  travels.
 *
 *  Handlers deeper in the tree win: a component that already consumed
 *  the paste (a composer textarea staging its own upload) calls
 *  `preventDefault()` on the way up, and this hook stands down. */
export function usePasteFiles(
  onFiles: (files: File[]) => void,
  enabled = true,
) {
  // Kept in a ref so a caller passing an inline arrow does not
  // resubscribe on every render.
  const onFilesRef = useRef(onFiles)
  useEffect(() => {
    onFilesRef.current = onFiles
  })

  useEffect(() => {
    if (!enabled) return
    const handlePaste = (e: ClipboardEvent) => {
      if (e.defaultPrevented) return
      const items = e.clipboardData?.items
      if (!items) return
      const files: File[] = []
      for (const item of Array.from(items)) {
        if (item.kind !== 'file') continue
        const f = item.getAsFile()
        if (f) files.push(f)
      }
      if (!files.length) return
      // A clipboard carrying an image usually carries a text twin as
      // well (a file path, an <img> tag). Swallow it, or the
      // attachment also lands as junk text in whatever field has focus.
      e.preventDefault()
      onFilesRef.current(files)
    }
    document.addEventListener('paste', handlePaste)
    return () => document.removeEventListener('paste', handlePaste)
  }, [enabled])
}

/**
 * Copy to the clipboard, falling back to a hidden-textarea copy when
 * the async Clipboard API is refused (some embedded browsers do).
 * Returns whether it worked, so the UI never claims a copy that did
 * not happen.
 *
 * Shared by the API-token modal (the one-time token snippets) and the
 * admin "Copy full key" action on every masked secret field.
 */
export const copyText = async (text: string): Promise<boolean> => {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

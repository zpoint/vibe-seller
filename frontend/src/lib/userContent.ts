/**
 * Split a stored user message into its text and its attachments.
 *
 * Attachments are encoded into a user message as markdown images
 * (``![filename](url)``) by the backend — chat sends AND a task's opening
 * message (create-time attachments), see app/attachment_markdown.py.
 * Parse them out so the bubble shows a thumbnail (or a PDF chip), never
 * the raw path/URL text.
 */
const ATTACHMENT_RE = /!\[([^\]]*)\]\(([^)]+)\)/g

export interface UserAttachment {
  alt: string
  url: string
}

export function parseUserContent(content: string): { text: string; images: UserAttachment[] } {
  const images: UserAttachment[] = []
  const text = content.replace(ATTACHMENT_RE, (_m, alt, url) => {
    images.push({ alt, url })
    return ''
  }).trim()
  return { text, images }
}

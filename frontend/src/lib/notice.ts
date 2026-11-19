/**
 * Backend-authored notices in a task transcript (role `notice`).
 *
 * The content is JSON `{kind, ...}` so the UI words it in the user's
 * language — see app/ai/unseen_images.py. Today's one kind:
 * `images_unseen` — the run's model cannot see the attached images; the
 * notice is written before the model answers.
 */
import type { TaskNotice } from '../types'

export const NOTICE_ROLE = 'notice'

export function parseNotice(content: string): TaskNotice | null {
  try {
    const n = JSON.parse(content)
    return n && typeof n.kind === 'string' ? n as TaskNotice : null
  } catch {
    return null
  }
}

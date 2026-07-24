import type { ConversationItem } from '../types'

/** A live image confirm card the user hasn't acted on yet: shown, and
 *  NOT resolved / expired / generating. */
export function hasPendingImageConfirm(items: ConversationItem[]): boolean {
  return items.some(
    it =>
      it.type === 'image_request' &&
      !!it.imageRequest &&
      !it.imageRequest.resolved &&
      !it.imageRequest.expired &&
      !it.imageRequest.generating,
  )
}

/** The agent is PARKED awaiting the user — a live confirm card, or a
 *  pending question — as opposed to actively working. A follow-up here
 *  redirects the agent immediately instead of queueing behind a step. */
export function isAwaitingUser(
  items: ConversationItem[],
  hasPendingQuestion: boolean,
): boolean {
  return hasPendingQuestion || hasPendingImageConfirm(items)
}

/** Where a composer send should go: during a pending question the message
 *  IS the answer, so route it through the free-text answer channel
 *  (unblocks the parked agent instantly, like the question card's "type
 *  freely"); otherwise send it as a normal chat message. The image
 *  confirm gate is interrupted server-side by POST /messages, so it uses
 *  the plain message path. */
export function composerSendKind(
  hasPendingQuestion: boolean,
  text: string,
): 'answer' | 'message' {
  return hasPendingQuestion && text.trim().length > 0 ? 'answer' : 'message'
}

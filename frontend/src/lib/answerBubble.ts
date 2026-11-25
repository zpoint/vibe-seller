/**
 * The user's answers to a parked question, as the server records them
 * (`answered_lines` in app/question_park.py): one `Q:` / `A:` pair per
 * question, a blank line between pairs. Anything else is an ordinary
 * message and returns null.
 */
export interface AnswerPair {
  question: string
  answer: string
}

export function parseAnswerBubble(content: string): AnswerPair[] | null {
  if (!content.startsWith('Q: ')) return null
  const pairs: AnswerPair[] = []
  let cur: { q: string[]; a: string[] | null } | null = null
  const flush = () => {
    if (cur && cur.a) pairs.push({ question: cur.q.join('\n').trim(), answer: cur.a.join('\n').trim() })
  }
  for (const line of content.split('\n')) {
    if (line.startsWith('Q: ') && (cur === null || cur.a !== null)) {
      flush()
      cur = { q: [line.slice(3)], a: null }
    } else if (line.startsWith('A: ') && cur && cur.a === null) {
      cur.a = [line.slice(3)]
    } else if (cur) {
      ;(cur.a ?? cur.q).push(line)
    }
  }
  if (!cur || cur.a === null) return null
  flush()
  return pairs
}

/**
 * Typing in the composer must not re-render the conversation's content.
 *
 * Observed: on a long task (~1000 stream items, a 200 KB report) typing
 * in the follow-up box lagged. The draft lives at the app root, so every
 * keystroke re-renders the stream with the same items but freshly made
 * callbacks — and each one re-parsed every message and the report as
 * markdown. Here a re-render with unchanged items must parse nothing.
 */
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { createRef, type ReactNode } from 'react'
import { I18nextProvider } from 'react-i18next'

let markdownRenders = 0
vi.mock('react-markdown', () => ({
  default: ({ children }: { children?: ReactNode }) => {
    markdownRenders += 1
    return <div>{children}</div>
  },
}))

import { ConversationStream } from '../components/conversation/ConversationStream'
import { i18nTestInstance, makeConversationItem, makeTask } from '../test/helpers'
import type { ConversationItem } from '../types'

function stream(items: ConversationItem[]) {
  // New function identities on every call, as the app root makes them.
  return (
    <I18nextProvider i18n={i18nTestInstance}>
      <ConversationStream
        items={items}
        todoItems={[]}
        task={makeTask({ status: 'completed' })}
        steps={[]}
        screenshots={{}}
        pendingQuestions={null}
        selectedAnswers={{}}
        otherInputs={{}}
        showOtherInput={{}}
        onSelectAnswer={() => {}}
        onToggleOther={() => {}}
        onSetOtherAnswer={() => {}}
        onSubmitAll={() => {}}
        onConfirmPlan={() => {}}
        onRequestChanges={() => {}}
        onOpenVisionSetup={() => {}}
        questionBannerRef={createRef<HTMLDivElement>()}
        isActive={false}
      />
    </I18nextProvider>
  )
}

describe('the stream does not re-parse unchanged content', () => {
  it('a re-render with the same items parses no markdown', () => {
    const items: ConversationItem[] = [
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'Reading the console' },
      }),
      makeConversationItem('result', { result: '# Report\n\nAll **done**.' }),
    ]
    const { rerender } = render(stream(items))
    const first = markdownRenders
    expect(first).toBeGreaterThan(0)

    rerender(stream(items)) // a keystroke in the composer
    rerender(stream(items))
    expect(markdownRenders).toBe(first)
  })

  it('a new message still renders', () => {
    const items: ConversationItem[] = [
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'First' },
      }),
    ]
    const { rerender, getByText } = render(stream(items))
    rerender(
      stream([
        ...items,
        makeConversationItem('agent_message', {
          message: { role: 'assistant', content: 'Second' },
        }),
      ]),
    )
    expect(getByText('Second')).toBeInTheDocument()
  })
})

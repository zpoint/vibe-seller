/**
 * A WAITING task says what it waits for.
 *
 * A parent waiting on its subtasks (`{strategy: 'children'}`) continues
 * by itself when the last one finishes. It must not read as "Manual"
 * with an empty reason and a 30-day deadline — that tells the user they
 * have to act. An agent-declared wait keeps its reason and deadline.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { I18nextProvider } from 'react-i18next'

import { ConversationStream } from '../components/conversation/ConversationStream'
import { i18nTestInstance, makeTask } from '../test/helpers'

function renderWaiting(waitCondition: string) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ConversationStream
        items={[]}
        todoItems={[]}
        task={makeTask({ status: 'waiting', wait_condition: waitCondition })}
        steps={[]}
        screenshots={{}}
        pendingQuestions={null}
        selectedAnswers={{}}
        otherInputs={{}}
        showOtherInput={{}}
        onSelectAnswer={() => {}}
        onSetAnswer={() => {}}
        onToggleOther={() => {}}
        onSetOtherAnswer={() => {}}
        onSubmitAll={() => {}}
        onConfirmPlan={() => {}}
        onRequestChanges={() => {}}
        onOpenVisionSetup={() => {}}
        questionBannerRef={createRef<HTMLDivElement>()}
        isActive={false}
      />
    </I18nextProvider>,
  )
}

describe('waiting card', () => {
  it('a parent waiting on subtasks says it continues by itself', () => {
    renderWaiting(JSON.stringify({ strategy: 'children' }))
    const t = i18nTestInstance.t
    expect(screen.getByText(t('waiting.subtasks'))).toBeTruthy()
    expect(screen.getByText(t('waiting.subtasksHint'))).toBeTruthy()
    expect(screen.queryByText(t('waiting.manual'))).toBeNull()
    expect(screen.queryByText(`${t('waiting.reason')}:`)).toBeNull()
    expect(screen.queryByText(`${t('waiting.maxDays')}:`)).toBeNull()
  })

  it('an agent-declared manual wait keeps its reason and deadline', () => {
    renderWaiting(
      JSON.stringify({
        check_strategy: 'manual',
        reason: 'Waiting for the supplier to confirm',
      }),
    )
    const t = i18nTestInstance.t
    expect(screen.getByText(t('waiting.manual'))).toBeTruthy()
    expect(screen.getByText('Waiting for the supplier to confirm')).toBeTruthy()
    expect(screen.getByText(`${t('waiting.maxDays')}:`)).toBeTruthy()
    expect(screen.queryByText(t('waiting.subtasksHint'))).toBeNull()
  })
})

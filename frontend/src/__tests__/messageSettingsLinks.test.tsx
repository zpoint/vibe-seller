/**
 * Links the agent's prompts tell it to write open a Settings tab in
 * place: no image key yet (#vision-setup → AI), a workplace sign-in or
 * permission expired (#integrations → Integrations, the WeCom / Feishu /
 * DingTalk rules in app/cli_integrations/skills.py). Any other link stays
 * a plain link.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MessageBubble } from '../components/conversation/MessageBubble'
import { MarkdownLink } from '../components/conversation/MarkdownLink'
import { TaskErrorNote } from '../components/TaskErrorNote'
import { createRef } from 'react'
import { I18nextProvider } from 'react-i18next'
import { ConversationStream } from '../components/conversation/ConversationStream'
import { i18nTestInstance, makeTask } from '../test/helpers'

describe('in-app settings links in an agent message', () => {
  it('an expired sign-in link opens Settings → Integrations', () => {
    const onOpenSettings = vi.fn()
    render(
      <MessageBubble
        role="assistant"
        content={'企业微信「邮件」权限已过期，请管理员重新授权：[设置 → 集成](#integrations)'}
        onOpenSettings={onOpenSettings}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /设置 → 集成/ }))
    expect(onOpenSettings).toHaveBeenCalledWith('integrations', undefined)
  })

  it("a platform's link opens Integrations at that platform's row", () => {
    const onOpenSettings = vi.fn()
    render(
      <MessageBubble role="assistant" content="[设置 → 集成](#integrations-wecom)" onOpenSettings={onOpenSettings} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /设置 → 集成/ }))
    expect(onOpenSettings).toHaveBeenCalledWith('integrations', 'cli-wecom')
  })

  it("a settings button's target does not depend on the agent's label language", () => {
    const onOpenSettings = vi.fn()
    render(
      <MarkdownLink href="#integrations-wecom" onOpenSettings={onOpenSettings}>
        设置 → 集成
      </MarkdownLink>,
    )
    fireEvent.click(screen.getByTestId('settings-link-integrations'))
    expect(onOpenSettings).toHaveBeenCalledWith('integrations', 'cli-wecom')
  })

  it('the image-key link still opens Settings → AI', () => {
    const onOpenSettings = vi.fn()
    render(
      <MessageBubble role="assistant" content="[Settings → AI → Vision](#vision-setup)" onOpenSettings={onOpenSettings} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Vision/ }))
    expect(onOpenSettings).toHaveBeenCalledWith('aiAgent', undefined)
  })

  it('any other link stays a link', () => {
    render(
      <MessageBubble role="assistant" content="[docs](https://docs.vibe-sellers.com/)" onOpenSettings={vi.fn()} />,
    )
    expect(screen.queryByRole('button')).toBeNull()
    expect((screen.getByRole('link', { name: 'docs' }) as HTMLAnchorElement).href).toBe('https://docs.vibe-sellers.com/')
  })

  it("a failed task's error shows the link's text and a button, never the markdown", () => {
    const onOpenSettings = vi.fn()
    render(
      <TaskErrorNote
        error={'企业微信「邮件」权限已过期，请管理员点「重新授权」：[设置 → 集成](#integrations-wecom)'}
        category="agent_reported"
        onOpenSettings={onOpenSettings}
      />,
    )
    const note = screen.getByTestId('task-error')
    expect(note.textContent).not.toContain('](#integrations')
    expect(note.textContent).toContain('设置 → 集成')
    fireEvent.click(screen.getByTestId('task-error-settings-integrations'))
    expect(onOpenSettings).toHaveBeenCalledWith('integrations', 'cli-wecom')
  })

  it('an error with no settings link renders as before', () => {
    render(<TaskErrorNote error="Something else broke [docs](https://x.example/)" category={null} onOpenSettings={vi.fn()} />)
    expect(screen.getByTestId('task-error').textContent).toBe('Something else broke [docs](https://x.example/)')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it("a task's result card turns the link into the same button", () => {
    const onOpenSettings = vi.fn()
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <ConversationStream
          items={[{
            id: 'r1', type: 'result', timestamp: '2026-11-27T00:00:00Z',
            result: '「邮件」权限已过期。请管理员打开 [Settings → Integrations](#integrations-wecom) 点「重新授权」。',
          }]}
          todoItems={[]}
          task={makeTask({ status: 'failed' })}
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
          onOpenSettings={onOpenSettings}
          questionBannerRef={createRef<HTMLDivElement>()}
          isActive={false}
        />
      </I18nextProvider>,
    )
    expect(screen.queryByRole('link', { name: /Settings → Integrations/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Settings → Integrations/ }))
    expect(onOpenSettings).toHaveBeenCalledWith('integrations', 'cli-wecom')
  })
})

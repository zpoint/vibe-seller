/** A failed task gets a re-authorize button even when the agent wrote no
 * markdown link — but only for an enabled platform the server says is
 * expired, and only when the error names that platform. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TaskErrorNote } from '../components/TaskErrorNote'
import { I18nextProvider } from 'react-i18next'
import { i18nTestInstance } from '../test/helpers'

const attention = vi.hoisted(() => ({ items: [] as { key: string; title: string }[] }))
vi.mock('../hooks/useIntegrationAttention', () => ({
  useIntegrationAttention: () => attention.items,
  INTEGRATION_ATTENTION_EVENT: 'vibe:integration-attention',
}))

describe('failed task re-authorize fallback', () => {
  beforeEach(() => { attention.items = [] })

  it('offers the enabled expired platform named in a plain-text error', () => {
    attention.items = [{ key: 'wecom', title: 'WeCom (企业微信)' }]
    const onOpenSettings = vi.fn()
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <TaskErrorNote
          error="无法读取企业微信邮箱：授权已过期，需要管理员重新授权。"
          category="agent_reported"
          onOpenSettings={onOpenSettings}
        />
      </I18nextProvider>,
    )
    expect(screen.getByTestId('task-error').textContent).toContain('WeCom authorization has expired')
    expect(screen.getByTestId('task-error').textContent).not.toContain('无法读取企业微信邮箱')
    fireEvent.click(screen.getByTestId('task-error-settings-integrations'))
    expect(onOpenSettings).toHaveBeenCalledWith('integrations', 'cli-wecom')
  })

  it('never offers a platform that was not connected', () => {
    render(
      <TaskErrorNote
        error="无法读取企业微信邮箱：授权已过期。"
        category="agent_reported"
        onOpenSettings={vi.fn()}
      />,
    )
    expect(screen.queryByTestId('task-error-settings-integrations')).toBeNull()
  })

  it('does not attach an unrelated platform to every failure', () => {
    attention.items = [{ key: 'wecom', title: 'WeCom (企业微信)' }]
    render(
      <TaskErrorNote error="Network timed out" category="agent_reported" onOpenSettings={vi.fn()} />,
    )
    expect(screen.queryByTestId('task-error-settings-integrations')).toBeNull()
  })
})

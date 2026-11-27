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
    expect(onOpenSettings).toHaveBeenCalledWith('integrations')
  })

  it('the image-key link still opens Settings → AI', () => {
    const onOpenSettings = vi.fn()
    render(
      <MessageBubble role="assistant" content="[Settings → AI → Vision](#vision-setup)" onOpenSettings={onOpenSettings} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Vision/ }))
    expect(onOpenSettings).toHaveBeenCalledWith('aiAgent')
  })

  it('any other link stays a link', () => {
    render(
      <MessageBubble role="assistant" content="[docs](https://docs.vibe-sellers.com/)" onOpenSettings={vi.fn()} />,
    )
    expect(screen.queryByRole('button')).toBeNull()
    expect((screen.getByRole('link', { name: 'docs' }) as HTMLAnchorElement).href).toBe('https://docs.vibe-sellers.com/')
  })
})

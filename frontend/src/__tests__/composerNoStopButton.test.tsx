/**
 * The chat composer must never host a Stop control.
 *
 * It used to: the send slot rendered Send when the textarea had text and
 * a red Stop when it was empty — and sending is precisely what empties
 * it. Measured on a live task, Stop landed inside Send's footprint
 * (Send x1110-1176, Stop x1121-1176, identical y and height), so the
 * click that sent a message left a destructive button under the cursor.
 * A double-click, or one "did that register?" click, stopped the run
 * with no confirmation — the user reported a task stopping that they
 * never knowingly stopped.
 *
 * Stop lives in the task header instead, at a fixed position that never
 * hosts another action. Rule: a destructive control must never occupy
 * the position a safe control just vacated.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChatComposer } from '../components/conversation/ChatComposer'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: string) => {
      const map: Record<string, string> = {
        'tasks.send': 'Send',
        'tasks.stopTask': 'Stop',
        'tasks.queuedWhileWorking': 'queued',
      }
      return map[k] ?? d ?? k
    },
  }),
}))

function props(over: Record<string, unknown> = {}) {
  return {
    fileInputRef: { current: null },
    uploading: false,
    uploadFiles: vi.fn(),
    attachments: [],
    onRemoveAttachment: vi.fn(),
    inputRef: { current: null },
    input: '',
    setInput: vi.fn(),
    hasContent: false,
    canSend: true,
    isActive: true,
    onSend: vi.fn(),
    placeholder: 'Type…',
    ...over,
  }
}

describe('ChatComposer send slot', () => {
  it('shows NO Stop button while the task is active and the box is empty', () => {
    // The exact state that sending creates, and where the red Stop used
    // to appear under the cursor.
    render(<ChatComposer {...props({ isActive: true, hasContent: false })} />)

    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
    const send = screen.getByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()
  })

  it('shows an enabled Send once there is content', () => {
    render(<ChatComposer {...props({ isActive: true, hasContent: true })} />)

    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })

  it('never renders a destructive (red) button in the composer', () => {
    // Guards the whole class, not just the label: no bg-red-* control
    // may sit in the send row in ANY active/content combination.
    for (const isActive of [true, false]) {
      for (const hasContent of [true, false]) {
        const { container, unmount } = render(
          <ChatComposer {...props({ isActive, hasContent })} />,
        )
        const red = container.querySelectorAll('[class*="bg-red-"]')
        expect(
          red.length,
          `red control rendered for isActive=${isActive} hasContent=${hasContent}`,
        ).toBe(0)
        unmount()
      }
    }
  })
})

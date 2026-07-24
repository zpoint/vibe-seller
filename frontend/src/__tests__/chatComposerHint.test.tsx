/**
 * The composer must set the right expectation for a follow-up:
 *  - awaiting the user (confirm card / question) → "redirect" hint
 *  - actively working → "queued" hint
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChatComposer } from '../components/conversation/ChatComposer'

const base = {
  fileInputRef: { current: null },
  uploading: false,
  uploadFiles: vi.fn(),
  attachments: [],
  onRemoveAttachment: vi.fn(),
  inputRef: { current: null },
  input: 'it is one image, both are references',
  setInput: vi.fn(),
  hasContent: true,
  canSend: true,
  onSend: vi.fn(),
  onStop: vi.fn(),
  placeholder: '',
}

describe('ChatComposer follow-up hint', () => {
  it('shows the REDIRECT hint (not queued) when awaiting the user', () => {
    render(<ChatComposer {...base} isActive awaitingUser />)
    expect(screen.getByTestId('chat-redirect-hint')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-queued-hint')).toBeNull()
  })

  it('shows the QUEUED hint when actively working (not awaiting user)', () => {
    render(<ChatComposer {...base} isActive awaitingUser={false} />)
    expect(screen.getByTestId('chat-queued-hint')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-redirect-hint')).toBeNull()
  })
})

/**
 * Image-generation "generating…" indicator: after the user confirms, the
 * kie.ai call can take 1–2 min. The card must show a dedicated generating
 * state (not the generic "handled" footer, and not the plain spinner) so
 * the user knows work is in progress.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ImageRequestCard } from '../components/conversation/ImageRequestCard'

const base = {
  taskId: 't1', requestId: 'r1', prompt: 'white bg', model: 'nano-banana-pro',
  models: ['nano-banana-pro'], referenceImages: [] as string[],
  onDecision: vi.fn(),
}

describe('ImageRequestCard generating state', () => {
  it('shows the generating indicator when resolved + generating', () => {
    render(<ImageRequestCard {...base} resolved generating />)
    expect(screen.getByTestId('image-card-generating')).toBeInTheDocument()
    // Not the terminal "handled" footer.
    expect(screen.queryByTestId('image-card-footer')).toBeNull()
    // Confirm/Cancel controls are gone once resolved.
    expect(screen.queryByTestId('image-confirm-btn')).toBeNull()
  })

  it('shows the handled footer when resolved and no longer generating', () => {
    render(<ImageRequestCard {...base} resolved generating={false} />)
    expect(screen.getByTestId('image-card-footer')).toBeInTheDocument()
    expect(screen.queryByTestId('image-card-generating')).toBeNull()
  })

  it('shows the confirm controls (no generating state) before resolution', () => {
    render(<ImageRequestCard {...base} resolved={false} />)
    expect(screen.getByTestId('image-confirm-btn')).toBeInTheDocument()
    expect(screen.queryByTestId('image-card-generating')).toBeNull()
  })
})

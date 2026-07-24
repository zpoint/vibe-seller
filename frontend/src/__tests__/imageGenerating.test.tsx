/**
 * Image-generation "generating…" indicator: after the user confirms, the
 * kie.ai call can take 1–2 min. The card must show a dedicated generating
 * state (not the generic "handled" footer, and not the plain spinner) so
 * the user knows work is in progress.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { ImageRequestCard } from '../components/conversation/ImageRequestCard'

const base = {
  taskId: 't1', requestId: 'r1', prompt: 'white bg', model: 'nano-banana-pro',
  models: [{
    id: 'nano-banana-pro', provider: 'Google', label: 'Nano Banana Pro',
    usd: 0.09, cny: 0.65, default: true,
  }],
  referenceImages: [] as string[],
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

  it('shows a truthful "you replied instead" footer when interrupted', () => {
    render(<ImageRequestCard {...base} resolved interrupted generating={false} />)
    const footer = screen.getByTestId('image-card-footer')
    // The interrupted key (t returns the key in these tests), NOT the
    // misleading "expired/replaced by a newer request" copy.
    expect(footer.textContent).toContain('vision.interrupted')
    expect(footer.textContent).not.toContain('vision.expired')
    // Non-actionable: no confirm/cancel buttons remain.
    expect(screen.queryByTestId('image-confirm-btn')).toBeNull()
  })
})

describe('ImageRequestCard model selector', () => {
  const multi = {
    ...base,
    model: 'nano-banana-pro',
    // Deliberately NOT in price order, to prove the submenu re-sorts.
    models: [
      { id: 'nano-banana-2', provider: 'Google', label: 'Nano Banana 2 · 1K', usd: 0.04, cny: 0.29 },
      { id: 'nano-banana-pro', provider: 'Google', label: 'Nano Banana Pro · 2K', usd: 0.09, cny: 0.65, default: true },
      { id: 'gpt-image-2', provider: 'OpenAI', label: 'GPT Image 2 · 2K', usd: 0.05, cny: 0.36 },
    ],
  }

  it('is a left→right cascade: providers → models, most expensive first', () => {
    render(<ImageRequestCard {...multi} resolved={false} />)
    // Custom popover, not a native <select>/second provider dropdown.
    expect(screen.queryByTestId('image-provider-select')).toBeNull()
    const trigger = screen.getByTestId('image-model-select')
    expect(trigger.textContent).toContain('Nano Banana Pro')

    // Open the cascade.
    fireEvent.click(trigger)
    const menu = screen.getByTestId('image-model-menu')
    // Level 1 — providers, de-duplicated.
    expect(within(menu).getByTestId('image-provider-Google')).toBeInTheDocument()
    expect(within(menu).getByTestId('image-provider-OpenAI')).toBeInTheDocument()

    // Level 2 — active provider is the selected model's (Google); its
    // models are sorted most-expensive-first regardless of input order.
    const googleIds = within(screen.getByTestId('image-model-submenu'))
      .getAllByRole('button')
      .map(b => b.getAttribute('data-model-id'))
    expect(googleIds).toEqual(['nano-banana-pro', 'nano-banana-2'])

    // Hovering another provider swaps the right column.
    fireEvent.mouseEnter(within(menu).getByTestId('image-provider-OpenAI'))
    const openaiIds = within(screen.getByTestId('image-model-submenu'))
      .getAllByRole('button')
      .map(b => b.getAttribute('data-model-id'))
    expect(openaiIds).toEqual(['gpt-image-2'])

    // Picking a model closes the menu and updates the trigger.
    fireEvent.click(
      within(screen.getByTestId('image-model-submenu')).getAllByRole('button')[0],
    )
    expect(screen.queryByTestId('image-model-menu')).toBeNull()
    expect(screen.getByTestId('image-model-select').textContent).toContain(
      'GPT Image 2',
    )
  })
})

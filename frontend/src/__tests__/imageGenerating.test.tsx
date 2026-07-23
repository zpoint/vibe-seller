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
})

describe('ImageRequestCard model selector', () => {
  const multi = {
    ...base,
    models: [
      { id: 'nano-banana-pro', provider: 'Google', label: 'Nano Banana Pro', usd: 0.09, cny: 0.65, default: true },
      { id: 'nano-banana-2', provider: 'Google', label: 'Nano Banana 2', usd: 0.04, cny: 0.29 },
      { id: 'gpt-image-2', provider: 'OpenAI', label: 'GPT Image 2', usd: 0.05, cny: 0.36 },
    ],
  }

  it('offers a Provider level and a Model level, with a price hint', () => {
    render(<ImageRequestCard {...multi} resolved={false} />)
    const providerSel = screen.getByTestId('image-provider-select') as HTMLSelectElement
    const modelSel = screen.getByTestId('image-model-select') as HTMLSelectElement
    // Two providers, de-duplicated.
    const provOpts = Array.from(providerSel.querySelectorAll('option')).map(o => o.textContent)
    expect(provOpts).toEqual(['Google', 'OpenAI'])
    // Default provider (Google) → only its two models are listed.
    const modelOpts = Array.from(modelSel.querySelectorAll('option'))
    expect(modelOpts.map(o => o.value)).toEqual(['nano-banana-pro', 'nano-banana-2'])
    // USD hint on the option label by default (test i18n has no zh).
    expect(modelOpts[0].textContent).toContain('$0.09')
    // The standalone hint row is present for the selected model.
    expect(screen.getByTestId('image-price-hint')).toBeInTheDocument()
  })
})

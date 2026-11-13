/**
 * Assistant bubbles render GFM tables — with the SAME remark config
 * the task conversation uses.
 *
 * The task conversation passes remark-gfm; the workspace assistant
 * rendered react-markdown with no plugins, so a `| a | b |` table —
 * exactly what the assistant produces when it summarizes stores —
 * showed as raw text. The config is now shared (lib/markdownPlugins),
 * so a call site cannot silently drop it again.
 *
 * On origin/main the assistant view has no remark plugins and this
 * fails with the raw pipes on screen — the negative control.
 */
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { WorkspaceAssistantView } from '../views/WorkspaceAssistantView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

const TABLE = [
  '| marketplace | spend |',
  '| --- | --- |',
  '| US | 1200 |',
  '| EU | 300 |',
].join('\n')

function stubAuthProbe() {
  // The view probes /api/auth/me for its debug toggle on mount.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      ({ ok: true, json: async () => ({ debug_mode: false }) }) as Response
    ),
  )
}

function renderAssistant(content: string) {
  stubAuthProbe()
  return render(
    <WorkspaceAssistantView
      messages={[
        { role: 'user', content: 'summarize spend by marketplace' },
        { role: 'assistant', content },
      ]}
      isRunning={false}
      onSendMessage={vi.fn()}
      onStop={vi.fn()}
    />,
  )
}

describe('workspace assistant markdown', () => {
  it('renders a GFM table as a real table', () => {
    const { container } = renderAssistant(TABLE)

    const table = container.querySelector('table')
    expect(table).not.toBeNull()
    expect(table!.querySelectorAll('thead th').length).toBe(2)
    expect(table!.querySelectorAll('tbody tr').length).toBe(2)
    expect(table!.textContent).toContain('1200')
    // The raw pipe form must be gone, not merely accompanied by a table.
    expect(container.textContent).not.toContain('| marketplace | spend |')
  })

  it('renders a table in a streaming bubble too', () => {
    // The live answer arrives as a _streaming message before the turn
    // closes — the same raw-table bug applies while it streams.
    stubAuthProbe()
    const { container } = render(
      <WorkspaceAssistantView
        messages={[{ role: '_streaming', content: TABLE }]}
        isRunning
        onSendMessage={vi.fn()}
        onStop={vi.fn()}
      />,
    )
    expect(container.querySelector('table')).not.toBeNull()
  })
})

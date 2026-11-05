import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('posthog-js', () => ({
  default: {
    init: vi.fn(),
    register: vi.fn(),
    unregister: vi.fn(),
    capture: vi.fn(),
    opt_out_capturing: vi.fn(),
  },
}))

import posthog from 'posthog-js'

const CONTENT = '详细的产品图片处理说明，包含品牌和SKU信息'

async function beforeSend(): Promise<(e: unknown) => unknown> {
  vi.resetModules()
  const mod = await import('../lib/telemetry')
  mod.initTelemetry('install-privacy-test', {})
  const cfg = (posthog.init as unknown as ReturnType<typeof vi.fn>).mock
    .calls[0][1] as { before_send: (e: unknown) => unknown }
  return cfg.before_send
}

describe('autocapture privacy', () => {
  beforeEach(() => {
    ;(posthog.init as unknown as ReturnType<typeof vi.fn>).mockClear()
  })

  it('records no content text for a click inside a task-description element', async () => {
    const run = await beforeSend()
    const out = run({
      event: '$autocapture',
      properties: {
        $el_text: CONTENT,
        $elements_chain:
          `div.ph-sensitive:text="${CONTENT}";` +
          `button:attr__class="ph-sensitive w-full":text="${CONTENT}"`,
        $current_url: 'http://localhost:7791/stores/1/tasks/2',
      },
    }) as { properties: Record<string, unknown> }
    const props = out.properties
    expect(String(props.$el_text ?? '')).not.toContain('产品图片')
    expect(String(props.action_label ?? '')).not.toContain('产品图片')
    expect(String(props.$elements_chain)).not.toContain('产品图片')
    expect(String(props.$elements_chain)).not.toContain('SKU')
  })

  it('keeps the label of a real control outside content', async () => {
    const run = await beforeSend()
    const out = run({
      event: '$autocapture',
      properties: {
        $el_text: 'New Task',
        $elements_chain:
          'button:attr__class="btn-primary":text="New Task"',
        $current_url: 'http://localhost:7791/tasks',
      },
    }) as { properties: Record<string, unknown> }
    expect(out.properties.action_label).toBe('New Task')
  })

  it('never promotes long free text into action_label', async () => {
    const run = await beforeSend()
    const long = 'x'.repeat(80)
    const out = run({
      event: '$autocapture',
      properties: {
        $el_text: long,
        $elements_chain: `span:attr__class="note":text="${long}"`,
      },
    }) as { properties: Record<string, unknown> }
    expect(String(out.properties.action_label ?? '')).not.toBe(long)
  })
})

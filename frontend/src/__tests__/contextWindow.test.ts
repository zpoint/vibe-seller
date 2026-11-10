/**
 * The chip badge shows the window Claude Code actually uses, not the
 * model's documented one: the same model with and without `[1m]` must
 * not both read "1M".
 */
import { describe, it, expect } from 'vitest'
import {
  claudeCodeWindow,
  effectiveWindowLabel,
  formatWindow,
  parseWindow,
} from '../components/contextWindow'

describe('effectiveWindowLabel', () => {
  it('tells a [1m] id from its bare twin (MiniMax, no override)', () => {
    expect(effectiveWindowLabel('MiniMax-M3.1-Flash[1m]', '1M')).toBe('1M')
    expect(effectiveWindowLabel('MiniMax-M3.1-Flash', '1M')).toBe('200K')
  })

  it('CLAUDE_CODE_MAX_CONTEXT_TOKENS wins over the suffix', () => {
    expect(effectiveWindowLabel('qwen3.8-flash', '1M', '1000000')).toBe('1M')
    expect(effectiveWindowLabel('auto', undefined, '983616')).toBe('984K')
  })

  it('never exceeds the model itself', () => {
    expect(effectiveWindowLabel('k3.1-pro', '256K', '1048576')).toBe('256K')
    expect(effectiveWindowLabel('glm-4.7', '200K')).toBe('200K')
  })

  it('parses and formats window sizes', () => {
    expect(parseWindow('256K')).toBe(256_000)
    expect(parseWindow('1M')).toBe(1_000_000)
    expect(parseWindow(undefined)).toBeUndefined()
    expect(formatWindow(1_048_576)).toBe('1M')
    expect(formatWindow(200_000)).toBe('200K')
    expect(claudeCodeWindow('x', 'not-a-number')).toBe(200_000)
  })
})

/**
 * The generic vendor picker's option-label rule, pinned shape by shape:
 * the human name (remote_env, else name) FIRST, then the platform, then
 * the ref — empty parts dropped with no dangling separators, the ref
 * dropped when it equals the display, and an unavailable row's reason
 * appended after an em dash exactly as the picker always did.
 */
import { describe, it, expect } from 'vitest'
import { vendorStoreOptionLabel } from '../lib/vendorStoreLabel'

describe('vendorStoreOptionLabel', () => {
  it('shows every part: display · platform · ref', () => {
    expect(vendorStoreOptionLabel({
      name: '4号店', platform: 'www.amazon.com',
      remote_env: '#4 美国站 (分组)', ref: 'p0000001',
    })).toBe('#4 美国站 (分组) · www.amazon.com · p0000001')
  })

  it('drops an empty platform with no dangling separator', () => {
    expect(vendorStoreOptionLabel({
      name: '4号店', platform: '', remote_env: '#4 美国站', ref: 'p0000001',
    })).toBe('#4 美国站 · p0000001')
  })

  it('falls back to the name when there is no remote_env', () => {
    expect(vendorStoreOptionLabel({
      name: '示例甲1店', platform: 'www.amazon.com', remote_env: '', ref: 'p0000001',
    })).toBe('示例甲1店 · www.amazon.com · p0000001')
  })

  it('drops the ref when it equals the display (listed by name)', () => {
    expect(vendorStoreOptionLabel({
      name: '示例甲1店', platform: '', remote_env: '', ref: '示例甲1店',
    })).toBe('示例甲1店')
    // The display wins: a ref matching it is duplication, not information.
    expect(vendorStoreOptionLabel({
      name: 'anything', platform: '', remote_env: '示例甲1店', ref: '示例甲1店',
    })).toBe('示例甲1店')
  })

  it('keeps the store name when remote_env heads the label (R1)', () => {
    // 悦互联's shape: no ref field — the row binds by its NAME, which
    // must survive beside the remote_env, not be replaced by it.
    expect(vendorStoreOptionLabel({
      name: '示例甲1店', platform: 'www.amazon.com',
      remote_env: '#4 美国站 (分组)', ref: '',
    })).toBe('#4 美国站 (分组) · www.amazon.com · 示例甲1店')
    // Same shape with a ref that equals the name: shown once, at the end.
    expect(vendorStoreOptionLabel({
      name: '示例乙2店', platform: 'www.amazon.com',
      remote_env: '#3 美国站', ref: '示例乙2店',
    })).toBe('#3 美国站 · www.amazon.com · 示例乙2店')
  })

  it('a row empty except the ref shows just the ref', () => {
    expect(vendorStoreOptionLabel({
      name: '', platform: '', remote_env: '', ref: 'p0000001',
    })).toBe('p0000001')
  })

  it('appends an unavailable row\'s reason after an em dash', () => {
    expect(vendorStoreOptionLabel(
      { name: '示例甲2店', platform: '', remote_env: '', ref: '' },
      'no device bound',
    )).toBe('示例甲2店 — no device bound')
    // With no reason text, nothing is appended.
    expect(vendorStoreOptionLabel(
      { name: '示例甲2店', platform: '', remote_env: '', ref: '' },
    )).toBe('示例甲2店')
  })
})

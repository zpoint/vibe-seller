/**
 * The generic vendor picker's option-label rule, pinned shape by shape.
 * ONE rule decided only from the row's own fields (ref vs name):
 *
 * - LISTED BY NAME (ref empty, or ref === name — 悦互联 / 稳卖):
 *   store name first: `{name} · {remote_env} · {platform}`.
 * - OPAQUE ref (ref non-empty, differs from name — AdsPower /
 *   BitBrowser): `{display} · {platform} · {ref}`, display =
 *   remote_env || name.
 *
 * In both, empty parts drop, a part equal to an earlier one drops, the
 * bind value is always visible, and an unavailable row's reason is
 * appended after an em dash.
 */
import { describe, it, expect } from 'vitest'
import { vendorStoreOptionLabel } from '../lib/vendorStoreLabel'

describe('vendorStoreOptionLabel — listed by name', () => {
  it('shows the store name first: name · remote_env · platform', () => {
    expect(vendorStoreOptionLabel({
      name: '示例甲1店', platform: 'www.amazon.com',
      remote_env: '#4 美国站 (分组)', ref: '',
    })).toBe('示例甲1店 · #4 美国站 (分组) · www.amazon.com')
    // ref === name is the same shape (the row still lists by name).
    expect(vendorStoreOptionLabel({
      name: '示例甲1店', platform: 'www.amazon.com',
      remote_env: '#4 美国站 (分组)', ref: '示例甲1店',
    })).toBe('示例甲1店 · #4 美国站 (分组) · www.amazon.com')
  })

  it('drops an empty remote_env or platform with no dangling separator', () => {
    expect(vendorStoreOptionLabel({
      name: '示例甲3店', platform: 'www.amazon.com', remote_env: '', ref: '',
    })).toBe('示例甲3店 · www.amazon.com')
    expect(vendorStoreOptionLabel({
      name: '示例甲2店', platform: '', remote_env: '#5 日本站', ref: '',
    })).toBe('示例甲2店 · #5 日本站')
    expect(vendorStoreOptionLabel({
      name: '示例甲3店', platform: '', remote_env: '', ref: '',
    })).toBe('示例甲3店')
  })

  it('shows a remote_env equal to the name only once', () => {
    expect(vendorStoreOptionLabel({
      name: '示例甲1店', platform: 'www.amazon.com',
      remote_env: '示例甲1店', ref: '',
    })).toBe('示例甲1店 · www.amazon.com')
  })

  it('never loses the store name (R1) — it leads the label', () => {
    expect(vendorStoreOptionLabel({
      name: '示例甲1店', platform: 'www.amazon.com',
      remote_env: '#4 美国站 (分组)', ref: '',
    })).toContain('示例甲1店')
  })
})

describe('vendorStoreOptionLabel — opaque ref', () => {
  it('shows display · platform · ref', () => {
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

  it('remote_env empty → the name leads', () => {
    expect(vendorStoreOptionLabel({
      name: '示例丁店', platform: 'www.amazon.com', remote_env: '', ref: 'ap-7788',
    })).toBe('示例丁店 · www.amazon.com · ap-7788')
  })

  it('a row empty except the ref shows just the ref', () => {
    expect(vendorStoreOptionLabel({
      name: '', platform: '', remote_env: '', ref: 'p0000001',
    })).toBe('p0000001')
  })

  it('never loses the bind value (R1) — the ref is shown', () => {
    expect(vendorStoreOptionLabel({
      name: '示例乙1店', platform: 'www.amazon.com',
      remote_env: '#3 美国站', ref: 'wm-0001',
    })).toBe('#3 美国站 · www.amazon.com · wm-0001')
  })
})

describe('vendorStoreOptionLabel — unavailable rows', () => {
  it('appends the reason after an em dash, in both shapes', () => {
    expect(vendorStoreOptionLabel(
      { name: '示例甲4店', platform: 'www.amazon.com',
        remote_env: '#7 美国站', ref: '' },
      'no device bound',
    )).toBe('示例甲4店 · #7 美国站 · www.amazon.com — no device bound')
    expect(vendorStoreOptionLabel(
      { name: '示例丙店', platform: '', remote_env: '', ref: 'ff-3321' },
      'not a Chrome kernel',
    )).toBe('示例丙店 · ff-3321 — not a Chrome kernel')
    // With no reason text, nothing is appended.
    expect(vendorStoreOptionLabel(
      { name: '示例甲2店', platform: '', remote_env: '', ref: '' },
    )).toBe('示例甲2店')
  })
})

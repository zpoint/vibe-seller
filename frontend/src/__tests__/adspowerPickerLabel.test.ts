/**
 * AdsPower's picker rows through #346's ONE label builder (R18).
 *
 * The row below is the REAL recorded parse_stores shape (the same
 * fixture the python test uses — tests/unit/test_browser/
 * test_adspower_client.py test_picker_row_binds_by_the_profile_id,
 * scrubbed placeholders: id kaaaaaa1, name 示例甲1店, group acme).
 * AdsPower is an opaque-ref vendor: ref (the profile id, the bind
 * value) differs from name (the profile's own name), so the label
 * must render display-first and the id LAST — never id-first (the
 * R18 vision finding on c71e4d77).
 */
import { describe, it, expect } from 'vitest'
import { vendorStoreOptionLabel } from '../lib/vendorStoreLabel'

/** The recorded parse_stores row for one AdsPower profile. */
const recordedRow = {
  name: '示例甲1店',
  platform: 'https://www.amazon.com',
  remote_env: '#1 示例甲1店 (acme)',
  ref: 'kaaaaaa1',
}

describe('vendorStoreOptionLabel — AdsPower recorded row', () => {
  it('shows the display first and the profile id last', () => {
    expect(vendorStoreOptionLabel(recordedRow)).toBe(
      '#1 示例甲1店 (acme) · https://www.amazon.com · kaaaaaa1',
    )
  })

  it('never leads with the profile id (the R18 regression)', () => {
    expect(vendorStoreOptionLabel(recordedRow).startsWith('kaaaaaa1')).toBe(
      false,
    )
  })

  it('a profile with no domain drops the platform, no dangling separator', () => {
    expect(
      vendorStoreOptionLabel({ ...recordedRow, platform: '' }),
    ).toBe('#1 示例甲1店 (acme) · kaaaaaa1')
  })

  it('a profile with no serial or group falls back to the bare name', () => {
    expect(
      vendorStoreOptionLabel({ ...recordedRow, remote_env: '示例甲1店' }),
    ).toBe('示例甲1店 · https://www.amazon.com · kaaaaaa1')
  })

  it('the bind value stays visible: the ref is the last part', () => {
    const label = vendorStoreOptionLabel(recordedRow)
    expect(label.split(' · ').pop()).toBe('kaaaaaa1')
  })
})

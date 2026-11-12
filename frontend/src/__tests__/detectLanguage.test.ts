/**
 * detectLanguage: saved choice > any zh in the browser language list >
 * mainland-China OS time zone > English. The time-zone branch is what
 * opens a Chinese Windows (running an English browser) in Chinese.
 */
import { describe, it, expect } from 'vitest'
import { detectLanguage } from '../i18n'

describe('detectLanguage', () => {
  it('a saved choice wins over everything', () => {
    expect(
      detectLanguage({ saved: 'en', languages: ['zh-CN'], timeZone: 'Asia/Shanghai' }),
    ).toBe('en')
    expect(
      detectLanguage({ saved: 'zh', languages: ['en-US'], timeZone: 'America/New_York' }),
    ).toBe('zh')
  })

  it('a saved value that is not en/zh is ignored', () => {
    expect(
      detectLanguage({ saved: 'fr', languages: ['zh-TW'], timeZone: 'UTC' }),
    ).toBe('zh')
  })

  it('ANY zh entry in the language list selects zh', () => {
    expect(
      detectLanguage({ saved: null, languages: ['en-US', 'zh-CN'], timeZone: 'UTC' }),
    ).toBe('zh')
    expect(
      detectLanguage({ saved: null, languages: ['zh-HK'], timeZone: 'UTC' }),
    ).toBe('zh')
  })

  it('an en-only browser on a mainland-China OS time zone is zh', () => {
    for (const tz of [
      'Asia/Shanghai', 'Asia/Chongqing', 'Asia/Chungking', 'Asia/Harbin',
      'Asia/Urumqi', 'Asia/Kashgar', 'PRC',
    ]) {
      expect(
        detectLanguage({ saved: null, languages: ['en-US'], timeZone: tz }),
      ).toBe('zh')
    }
  })

  it('en-only languages outside the CN time zones stay en', () => {
    expect(
      detectLanguage({ saved: null, languages: ['en-US'], timeZone: 'America/New_York' }),
    ).toBe('en')
    expect(
      detectLanguage({ saved: null, languages: ['en-GB'], timeZone: 'Asia/Tokyo' }),
    ).toBe('en')
    expect(
      detectLanguage({ saved: null, languages: [], timeZone: '' }),
    ).toBe('en')
  })
})

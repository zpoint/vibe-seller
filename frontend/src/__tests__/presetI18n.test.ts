/**
 * Preset display strings come from the server catalog in the UI's
 * language: the frontend only picks `i18n[lang]` and falls back to the
 * entry's English text. It holds no translation table of its own, so a
 * catalog update can change them without a frontend release.
 */
import { describe, it, expect } from 'vitest'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { baseLang, localizePresets } from '../components/presetI18n'

const PRESETS = {
  qwen: {
    name: 'Qwen (Pay-as-you-go, China)',
    group: 'Alibaba Cloud',
    variant: 'Pay-as-you-go (China)',
    i18n: {
      zh: {
        name: 'Qwen（按量付费，国内）',
        group: '阿里云',
        variant: '按量付费（国内）',
      },
    },
    models: [
      {
        label: 'Qwen3.7-Max (previous flagship)',
        i18n: { zh: { label: 'Qwen3.7-Max（上代旗舰）' } },
      },
      { label: 'Qwen3-VL-Plus' },
    ],
  },
}

describe('localizePresets', () => {
  it('uses the catalog translation for the UI language', () => {
    const out = localizePresets(PRESETS, 'zh-CN')
    expect(out.qwen.name).toBe('Qwen（按量付费，国内）')
    expect(out.qwen.variant).toBe('按量付费（国内）')
    expect(out.qwen.groupLabel).toBe('阿里云')
    expect(out.qwen.models?.[0].label).toBe('Qwen3.7-Max（上代旗舰）')
  })

  it('falls back to the English text and keeps the group key', () => {
    const zh = localizePresets(PRESETS, 'zh')
    expect(zh.qwen.models?.[1].label).toBe('Qwen3-VL-Plus')
    expect(zh.qwen.group).toBe('Alibaba Cloud')
    const en = localizePresets(PRESETS, 'en')
    expect(en.qwen.variant).toBe('Pay-as-you-go (China)')
    expect(en.qwen.groupLabel).toBe('Alibaba Cloud')
  })

  it('normalises region-tagged languages', () => {
    expect(baseLang('zh-CN')).toBe('zh')
    expect(baseLang(undefined)).toBe('en')
  })

  it('keeps no preset translation table in the frontend', () => {
    const profiles = (zhTranslation as { profiles: Record<string, unknown> })
      .profiles
    expect(profiles.presetTerms).toBeUndefined()
  })
})

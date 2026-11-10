/**
 * Provider-preset display strings follow the UI language.
 *
 * The server sends one English copy ("Pay-as-you-go (China)",
 * "Qwen3.8-Flash (fast, vision)"); a Chinese UI must not show it raw.
 */
import { describe, it, expect } from 'vitest'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { localizePresetText, localizePresets } from '../components/presetI18n'

const tFor = (lng: string) => {
  const inst = i18n.createInstance()
  inst.init({
    resources: {
      en: { translation: enTranslation },
      zh: { translation: zhTranslation },
    },
    lng,
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })
  return inst.t
}

describe('localizePresetText', () => {
  const zh = tFor('zh')
  const en = tFor('en')

  it('translates variants, groups and model tags in Chinese', () => {
    expect(localizePresetText('Pay-as-you-go (China)', zh)).toBe(
      '按量付费（国内）',
    )
    expect(localizePresetText('International', zh)).toBe('国际')
    expect(localizePresetText('Alibaba Cloud', zh)).toBe('阿里云')
    expect(localizePresetText('Token Plan (Singapore cluster)', zh)).toBe(
      'Token Plan（新加坡集群）',
    )
    expect(localizePresetText('Qwen3.8-Flash (fast, vision)', zh)).toBe(
      'Qwen3.8-Flash（快速，视觉）',
    )
    expect(
      localizePresetText(
        'Coding Plan (China, existing subscribers only)',
        zh,
      ),
    ).toBe('Coding Plan（国内，仅限老用户）')
  })

  it('leaves English and untranslated names unchanged', () => {
    expect(localizePresetText('Pay-as-you-go (China)', en)).toBe(
      'Pay-as-you-go (China)',
    )
    expect(localizePresetText('GLM-4.7', zh)).toBe('GLM-4.7')
    expect(localizePresetText('DeepSeek', zh)).toBe('DeepSeek')
  })

  it('keeps the group key stable and localises its label', () => {
    const out = localizePresets(
      {
        qwen: {
          name: 'Qwen (Pay-as-you-go, China)',
          group: 'Alibaba Cloud',
          variant: 'Pay-as-you-go (China)',
          models: [{ label: 'Qwen3.7-Max (flagship)' }],
        },
      },
      zh,
    )
    expect(out.qwen.group).toBe('Alibaba Cloud')
    expect(out.qwen.groupLabel).toBe('阿里云')
    expect(out.qwen.name).toBe('Qwen（按量付费，国内）')
    expect(out.qwen.models?.[0].label).toBe('Qwen3.7-Max（旗舰）')
  })
})

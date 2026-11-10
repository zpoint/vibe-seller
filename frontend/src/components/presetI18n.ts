/** Show provider-preset display strings in the UI's language.
 *
 *  Everything about a preset (order, values, links, and the
 *  translations of its name / group / variant and model labels) comes
 *  from the server's preset catalog, which syncs from the project's
 *  `main`, so a catalog update changes what users see without a
 *  frontend release. Each entry carries its English text plus an
 *  optional `i18n: {<lang>: {field: text}}`. This picks the field for
 *  the current language and falls back to the English text. No
 *  translation table lives in the frontend.
 */

type I18n<F extends string> = Partial<
  Record<string, Partial<Record<F, string>>>
>

interface LocalizablePreset {
  name: string
  group?: string
  variant?: string
  i18n?: I18n<'name' | 'group' | 'variant'>
  models?: { label: string; i18n?: I18n<'label'> }[]
}

/** `zh-CN` / `zh` -> `zh`; the catalog keys translations by base language. */
export function baseLang(language: string | undefined): string {
  return (language || 'en').split('-')[0].toLowerCase()
}

/** Return the presets with display strings in `language`. `group` stays
 *  the stable grouping key; its display text goes in `groupLabel`. */
export function localizePresets<P extends LocalizablePreset>(
  presets: Record<string, P>,
  language: string | undefined,
): Record<string, P & { groupLabel?: string }> {
  const lang = baseLang(language)
  const out: Record<string, P & { groupLabel?: string }> = {}
  for (const [id, p] of Object.entries(presets)) {
    const tr = p.i18n?.[lang] ?? {}
    out[id] = {
      ...p,
      name: tr.name ?? p.name,
      variant: tr.variant ?? p.variant,
      groupLabel: tr.group ?? p.group,
      models: p.models?.map((m) => ({
        ...m,
        label: m.i18n?.[lang]?.label ?? m.label,
      })),
    }
  }
  return out
}

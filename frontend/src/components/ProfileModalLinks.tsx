import { useTranslation } from 'react-i18next'

/** "Where do I get a key?" links under the API key field: the
 *  provider's own console (buy / top up / create a key) and our docs
 *  section that walks through it. Both come from the preset catalog
 *  (`purchase_url`, `guide_url[lang]`); nothing renders without them. */
export function ProfileModalLinks({
  purchaseUrl,
  guideUrl,
}: {
  purchaseUrl?: string
  guideUrl?: Record<string, string>
}) {
  const { t, i18n } = useTranslation()
  const lang = (i18n.language || 'en').startsWith('zh') ? 'zh' : 'en'
  const guide = guideUrl?.[lang] ?? guideUrl?.en
  if (!purchaseUrl && !guide) return null

  const link = 'text-indigo-600 hover:text-indigo-800 hover:underline'
  return (
    <div
      className="flex flex-wrap gap-x-4 gap-y-1 mt-1 text-xs"
      data-testid="profile-modal-links"
    >
      {purchaseUrl && (
        <a href={purchaseUrl} target="_blank" rel="noopener noreferrer" className={link}>
          {t('profiles.purchaseLink')} ↗
        </a>
      )}
      {guide && (
        <a href={guide} target="_blank" rel="noopener noreferrer" className={link}>
          {t('profiles.guideLink')} ↗
        </a>
      )}
    </div>
  )
}

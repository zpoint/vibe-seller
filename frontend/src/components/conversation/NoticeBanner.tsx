import { useTranslation } from 'react-i18next'
import type { TaskNotice } from '../../types'

/** A notice the backend wrote into the transcript (role `notice`). */
export function NoticeBanner({ notice }: { notice: TaskNotice }) {
  const { t } = useTranslation()
  if (notice.kind !== 'images_unseen') return null
  return (
    <div
      role="status"
      data-testid="notice-images-unseen"
      className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[14px] leading-relaxed text-amber-900"
    >
      <span aria-hidden="true">⚠️</span>
      <span>
        {t('tasks.noticeImagesUnseen', {
          model: notice.model || t('tasks.noticeThisModel'),
          files: (notice.files || []).join(', '),
        })}
      </span>
    </div>
  )
}

import { useTranslation } from 'react-i18next'

interface GeneratedImageCardProps {
  url: string
  path: string
  prompt?: string
  model?: string
  kind?: string
}

/** Inline display of an AI-generated image saved in the task workspace.
 *  Distinct from the finished-task file explorer — the image renders
 *  right where it was produced in the conversation. */
export function GeneratedImageCard({
  url,
  path,
  prompt,
  model,
  kind,
}: GeneratedImageCardProps) {
  const { t } = useTranslation()
  return (
    <div
      data-testid="generated-image-card"
      className="mb-4 bg-white rounded-2xl border border-gray-200 shadow-sm p-4"
    >
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm font-semibold text-gray-700">
          {t('vision.generatedTitle')}
        </span>
        {kind && (
          <span className="px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-semibold uppercase tracking-wide">
            {kind}
          </span>
        )}
        {model && (
          <span className="text-[11px] text-gray-400">{model}</span>
        )}
      </div>
      <a href={url} target="_blank" rel="noreferrer" className="block">
        <img
          data-testid="generated-image"
          src={url}
          alt={prompt || path}
          className="max-w-full rounded-lg border border-gray-100 shadow-sm"
        />
      </a>
      <div className="mt-2 text-[11px] text-gray-400 break-all">{path}</div>
    </div>
  )
}

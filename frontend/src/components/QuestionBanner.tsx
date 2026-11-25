import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { RefObject } from 'react'
import Markdown from 'react-markdown'
import { REMARK_PLUGINS } from '../lib/markdownPlugins'

// Agents write question text with single "\n" line breaks (bullets,
// bilingual sections, etc.). Markdown treats a single newline as a soft
// break — it collapses to a space — so the text renders as one run-on
// wall. Promote each newline to a hard break (two trailing spaces) so
// the author's line structure survives. (Bold/italic/lists/hr are plain
// CommonMark; remark-gfm below adds the GFM extensions — tables,
// strikethrough, task lists, autolinks.)
function preserveBreaks(text: string): string {
  return (text || '').replace(/\n/g, '  \n')
}

interface Question {
  header?: string
  question: string
  options?: { label: string; description?: string }[]
  multiSelect?: boolean
}

// A multi-select answer: the picked labels, then any typed text, joined
// as Claude Code's AskUserQuestion joins them.
function joinPicks(picks: string[], other: string | null): string {
  return [...picks, (other ?? '').trim()].filter(Boolean).join(', ')
}

interface QuestionBannerProps {
  bannerRef: RefObject<HTMLDivElement | null>
  questions: Question[]
  selectedAnswers: Record<string, string>
  showOtherInput: Record<string, boolean>
  otherInputs: Record<string, string>
  onSelectAnswer: (questionText: string, answer: string) => void
  onSetAnswer: (questionText: string, answer: string) => void
  onToggleOther: (questionText: string) => void
  onSetOtherAnswer: (questionText: string, text: string) => void
  onSubmitAll: (overrideAnswers?: Record<string, string>) => void
}

export function QuestionBanner({
  bannerRef,
  questions,
  selectedAnswers,
  showOtherInput,
  otherInputs,
  onSelectAnswer,
  onSetAnswer,
  onToggleOther,
  onSetOtherAnswer,
  onSubmitAll,
}: QuestionBannerProps) {
  const { t } = useTranslation()
  const [freeTextMode, setFreeTextMode] = useState(false)
  const [freeTextInput, setFreeTextInput] = useState('')
  // Multi-select questions keep their picks here; other text is null
  // while "Other" is closed.
  const [picks, setPicks] = useState<Record<string, string[]>>({})
  const [multiOther, setMultiOther] = useState<Record<string, string | null>>({})
  const updateMulti = (q: string, nextPicks: string[], nextOther: string | null) => {
    setPicks(p => ({ ...p, [q]: nextPicks }))
    setMultiOther(p => ({ ...p, [q]: nextOther }))
    onSetAnswer(q, joinPicks(nextPicks, nextOther))
  }

  return (
    <div ref={bannerRef} className="mb-4 rounded-xl border border-amber-200 bg-gradient-to-b from-amber-50 to-white overflow-hidden shadow-sm">
      {!freeTextMode && (
        <div className="divide-y divide-amber-100">
          {questions.map((q, qi) => {
            const multi = !!q.multiSelect
            const picked = picks[q.question] || []
            const otherText = multiOther[q.question] ?? null
            const isOther = multi ? otherText !== null : showOtherInput[q.question]
            const selectedOpt = (q.options || []).find(o => o.label === selectedAnswers[q.question])
            const showDesc = !multi && !isOther && selectedOpt?.description
            const isPicked = (label: string) =>
              multi ? picked.includes(label) : selectedAnswers[q.question] === label && !isOther
            const pick = (label: string) => {
              if (!multi) return onSelectAnswer(q.question, label)
              const next = picked.includes(label) ? picked.filter(l => l !== label) : [...picked, label]
              updateMulti(q.question, next, otherText)
            }
            const toggleOther = () =>
              multi ? updateMulti(q.question, picked, isOther ? null : '') : onToggleOther(q.question)
            const typeOther = (text: string) =>
              multi ? updateMulti(q.question, picked, text) : onSetOtherAnswer(q.question, text)

            return (
              <div key={qi} className="px-4 py-3 space-y-2">
                {/* Question label */}
                <div>
                  {q.header && (
                    <span className="px-1.5 py-0.5 bg-amber-200/80 text-amber-800 rounded text-[10px] font-semibold uppercase tracking-wide mr-2">
                      {q.header}
                    </span>
                  )}
                  <div className="text-sm font-medium text-gray-800 prose prose-sm max-w-none prose-p:my-1 prose-li:my-0.5 prose-ul:my-1 prose-headings:text-sm prose-headings:my-1">
                    <Markdown remarkPlugins={REMARK_PLUGINS}>
                      {preserveBreaks(q.question)}
                    </Markdown>
                  </div>
                  {multi && (
                    <div className="text-[11px] text-amber-700 mt-0.5" data-multi-select-hint>
                      {t('tasks.multiSelectHint')}
                    </div>
                  )}
                </div>
                {/* Options */}
                <div className="space-y-1.5">
                  <div className="flex flex-wrap gap-1.5">
                    {(q.options || []).map((opt, oi) => (
                      <button
                        key={oi}
                        onClick={() => pick(opt.label)}
                        aria-pressed={isPicked(opt.label)}
                        className={`px-3 py-2.5 text-sm sm:py-1.5 sm:text-xs border rounded-lg transition-all ${
                          isPicked(opt.label)
                            ? 'bg-indigo-50 border-indigo-400 text-indigo-700 font-medium shadow-sm'
                            : 'bg-white border-gray-200 text-gray-600 hover:bg-indigo-50 hover:border-indigo-300 hover:text-indigo-600'
                        }`}
                        title={opt.description || ''}
                      >
                        {opt.label}
                      </button>
                    ))}
                    <button
                      onClick={toggleOther}
                      className={`px-3 py-2.5 text-sm sm:py-1.5 sm:text-xs border rounded-lg transition-all ${
                        isOther
                          ? 'bg-indigo-50 border-indigo-400 text-indigo-700 font-medium shadow-sm'
                          : 'bg-white border-gray-200 text-gray-600 hover:bg-indigo-50 hover:border-indigo-300 hover:text-indigo-600'
                      }`}
                    >
                      {t('tasks.other')}
                    </button>
                  </div>
                  {showDesc && (
                    <p className="text-xs text-gray-500 pl-0.5">{selectedOpt.description}</p>
                  )}
                  {isOther && (
                    <input
                      value={multi ? otherText ?? '' : otherInputs[q.question] || ''}
                      onChange={e => typeOther(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Escape') toggleOther() }}
                      placeholder={t('tasks.typeAnswer')}
                      className="w-full px-3 py-2.5 text-sm sm:py-1.5 sm:text-xs border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400 bg-white"
                      autoFocus
                    />
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {freeTextMode && (
        <div className="px-4 py-3">
          <textarea
            value={freeTextInput}
            onChange={e => setFreeTextInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Escape') setFreeTextMode(false)
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && freeTextInput.trim()) {
                e.preventDefault()
                onSubmitAll({ _free_text: freeTextInput.trim() })
              }
            }}
            placeholder={t('tasks.freeTextPlaceholder')}
            aria-label={t('tasks.freeTextPlaceholder')}
            className="min-h-[80px] w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400 bg-white resize-y"
          />
        </div>
      )}
      {questions.length > 0 && (
        <div className="px-4 py-3 bg-amber-50/50 border-t border-amber-100 flex items-center justify-between">
          <button
            onClick={() => freeTextMode ? onSubmitAll({ _free_text: freeTextInput.trim() }) : onSubmitAll()}
            disabled={freeTextMode ? !freeTextInput.trim() : Object.keys(selectedAnswers).length < questions.length}
            className="px-5 py-2 text-xs font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shadow-sm"
          >
            {t('tasks.submitAnswers')}
          </button>
          <button
            type="button"
            className="text-xs text-indigo-600 hover:underline cursor-pointer"
            onClick={() => setFreeTextMode(prev => !prev)}
            aria-pressed={freeTextMode}
          >
            {freeTextMode ? t('tasks.backToOptions') : t('tasks.dismissQuestions')}
          </button>
        </div>
      )}
    </div>
  )
}

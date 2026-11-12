// The first-run guide: three short steps explaining the store model.
//
// The card is centered in EVERY viewport;
// the page dims around the step's [data-tour] anchor with a spotlight
// ring, so the highlighted element stays visible above the dim. The
// dim is four rects around the hole — no box-shadow trick — so the
// centered card never darkens it. Anchor missing -> dim only. The
// anchor is re-measured every frame while open: the store list loads
// after first paint and moves "All Stores" down.
// Hand-rolled on purpose: shepherd.js, a common tour library, is
// AGPL-3.0 since v10 — no fit for this Apache-2.0 repo.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { saveLanguagePreference } from '../i18n'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import { useIsMobile } from '../hooks/useIsMobile'
import { GUIDE_STEP_COUNT, type GuideController } from '../hooks/useOnboardingGuide'

const STEPS = [
  { id: 'allStores', anchor: 'all-stores', blocks: null },
  { id: 'store', anchor: 'new-store', blocks: null },
  // Step 3 explains the switch in BOTH scopes, as two labeled blocks.
  { id: 'taskType', anchor: 'task-type', blocks: ['inStore', 'inAllStores'] },
] as const

const HOLE_PAD = 6

interface Rect {
  left: number; top: number; width: number; height: number; bottom: number
}

function anchorRect(name: string): Rect | null {
  const el = document.querySelector(`[data-tour="${name}"]`)
  if (!el) return null
  const r = el.getBoundingClientRect()
  return r.width > 0 && r.height > 0 ? r : null
}

export function OnboardingGuide({ guide }: { guide: GuideController }) {
  const { t, i18n } = useTranslation()
  const isMobile = useIsMobile()
  const navigate = useNavigate()
  const pathname = useRouterState({ select: s => s.location.pathname })
  const [rect, setRect] = useState<Rect | null>(null)

  // The guide explains the tasks view with the store sidebar visible;
  // if replay was opened from another view, navigate there first.
  useEffect(() => {
    if (!guide.open) return
    if (pathname.startsWith('/workspace') || pathname.startsWith('/settings')) {
      navigate({ to: '/tasks' })
    }
  }, [guide.open, pathname, navigate])

  // All setState runs off-frame (rAF / event listeners), never
  // synchronously in the effect body; setRect fires only when the
  // geometry actually changed.
  useEffect(() => {
    if (!guide.open) return
    let raf = 0
    let alive = true
    const same = (a: Rect | null, b: Rect | null) =>
      a === b || (!!a && !!b && a.left === b.left && a.top === b.top
        && a.width === b.width && a.height === b.height)
    const measure = () => {
      const next = isMobile ? null : anchorRect(STEPS[guide.step].anchor)
      setRect(prev => (same(prev, next) ? prev : next))
    }
    const tick = () => {
      if (!alive) return
      measure()
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    window.addEventListener('resize', measure)
    document.addEventListener('scroll', measure, true)
    return () => {
      alive = false
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', measure)
      document.removeEventListener('scroll', measure, true)
    }
  }, [guide.open, guide.step, isMobile])

  if (!guide.open) return null

  const step = STEPS[guide.step]
  const last = guide.step + 1 >= GUIDE_STEP_COUNT
  const vw = window.innerWidth
  const vh = window.innerHeight
  const hole = rect && !isMobile ? {
    x: Math.max(rect.left - HOLE_PAD, 0),
    y: Math.max(rect.top - HOLE_PAD, 0),
    r: Math.min(rect.left + rect.width + HOLE_PAD, vw),
    b: Math.min(rect.top + rect.height + HOLE_PAD, vh),
  } : null

  // Same code path as the sidebar's LanguageSwitcher: the choice is
  // saved (per origin) and reported; it never stamps the flag and
  // never advances the step.
  const setLang = (lang: 'zh' | 'en') => {
    void i18n.changeLanguage(lang)
    saveLanguagePreference(lang)
    sendEvent(FrontendEvent.GUIDE_LANGUAGE_SWITCHED, { to: lang })
  }

  return createPortal(
    <div data-testid="onboarding-guide" className="fixed inset-0 z-[70]">
      {/* Transparent full-screen base: every click lands here, so the
          tour swallows interaction — including clicks in the bright
          spotlight hole. The dim rects above it are visual only. */}
      <div className="absolute inset-0" />
      {hole ? (
        <>
          <div className="absolute bg-black/50" style={{ left: 0, top: 0, width: vw, height: hole.y }} />
          <div className="absolute bg-black/50" style={{ left: 0, top: hole.b, width: vw, height: vh - hole.b }} />
          <div className="absolute bg-black/50" style={{ left: 0, top: hole.y, width: hole.x, height: hole.b - hole.y }} />
          <div className="absolute bg-black/50" style={{ left: hole.r, top: hole.y, width: vw - hole.r, height: hole.b - hole.y }} />
          <div
            className="absolute rounded-md ring-2 ring-indigo-500 pointer-events-none"
            style={{
              left: hole.x, top: hole.y,
              width: hole.r - hole.x, height: hole.b - hole.y,
            }}
          />
        </>
      ) : (
        <div className="absolute inset-0 bg-black/50" />
      )}
      <div
        role="dialog"
        aria-label={t('onboarding.title')}
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 bg-white rounded-xl shadow-2xl border border-gray-200 p-6 w-[min(92vw,480px)]"
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-semibold text-indigo-600">
            {t('onboarding.stepPosition', { current: guide.step + 1, total: GUIDE_STEP_COUNT })}
          </span>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 text-sm">
              <button
                onClick={() => setLang('zh')}
                className={i18n.language === 'zh' ? 'font-bold text-gray-900' : 'text-gray-400 hover:text-gray-600'}
              >中文</button>
              <span className="text-gray-300">|</span>
              <button
                onClick={() => setLang('en')}
                className={i18n.language === 'en' ? 'font-bold text-gray-900' : 'text-gray-400 hover:text-gray-600'}
              >English</button>
            </div>
            <button
              onClick={guide.dismiss}
              aria-label={t('common.close')}
              className="p-1 text-gray-400 hover:text-gray-600"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          </div>
        </div>
        <h2 className="text-lg font-semibold text-gray-900 mb-2">{t(`onboarding.${step.id}.title`)}</h2>
        {step.blocks ? (
          <div className="space-y-2">
            {step.blocks.map(b => (
              <p key={b} className="text-base leading-relaxed text-gray-600">
                <span className="font-semibold text-gray-800">{t(`onboarding.${step.id}.${b}Label`)}</span>
                {t(`onboarding.${step.id}.${b}`)}
              </p>
            ))}
          </div>
        ) : (
          <p className="text-base leading-relaxed text-gray-600">{t(`onboarding.${step.id}.body`)}</p>
        )}
        <div className="flex items-center justify-between mt-5 gap-2">
          <button onClick={guide.skip} className="px-3 py-2 text-sm text-gray-500 hover:text-gray-700">
            {t('onboarding.skip')}
          </button>
          <div className="flex items-center gap-2">
            {guide.step > 0 && (
              <button onClick={guide.prev} className="px-4 py-2 text-sm rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">
                {t('onboarding.back')}
              </button>
            )}
            <button onClick={guide.next} className="px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700">
              {last ? t('onboarding.done') : t('onboarding.next')}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}

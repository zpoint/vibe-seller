// The AI setup guide on Settings → AI (state: hooks/useAiSetupGuide.ts):
// the page dims around the "Add Model" button ([data-tour="add-model"])
// and a short bubble next to it says "click here". The button stays
// clickable through the hole — clicking it is the way forward, and
// closes the guide. Same dim-rects-around-a-hole drawing as
// OnboardingGuide; the anchor is re-measured every frame while open.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { aiDocsUrl } from '../lib/aiSetup'
import type { AiGuideController } from '../hooks/useAiSetupGuide'

const ANCHOR = '[data-tour="add-model"]'
const PAD = 6
const BUBBLE_W = 340

interface Hole { x: number; y: number; r: number; b: number }

function measure(): Hole | null {
  const el = document.querySelector(ANCHOR)
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (!r.width || !r.height) return null
  return { x: r.left - PAD, y: r.top - PAD, r: r.right + PAD, b: r.bottom + PAD }
}

export function AiSetupGuide({ guide }: { guide: AiGuideController }) {
  const { t, i18n } = useTranslation()
  const [hole, setHole] = useState<Hole | null>(null)
  const { open, close } = guide

  useEffect(() => {
    if (!open) return
    document.querySelector(ANCHOR)?.scrollIntoView?.({ block: 'center' })
    let raf = 0
    const tick = () => {
      const next = measure()
      setHole(prev => (prev && next && prev.x === next.x && prev.y === next.y
        && prev.r === next.r && prev.b === next.b ? prev : next))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    // Clicking the real button is "done with the guide".
    const onClick = (e: MouseEvent) => {
      if ((e.target as Element | null)?.closest?.(ANCHOR)) close('add_model')
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close('later') }
    document.addEventListener('click', onClick, true)
    document.addEventListener('keydown', onKey)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('click', onClick, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, close])

  if (!open) return null
  const vw = window.innerWidth
  const vh = window.innerHeight
  // Bubble under the button, right edges aligned; above it if no room.
  const left = hole ? Math.min(Math.max(hole.r - BUBBLE_W, 12), vw - BUBBLE_W - 12) : (vw - BUBBLE_W) / 2
  const below = !hole || hole.b + 220 < vh
  const top = hole ? (below ? hole.b + 12 : undefined) : vh / 3
  const bottom = hole && !below ? vh - hole.y + 12 : undefined

  return createPortal(
    <div data-testid="ai-setup-guide" className="fixed inset-0 z-[60] pointer-events-none">
      {hole ? (
        <>
          <div className="absolute bg-black/40 pointer-events-auto" style={{ left: 0, top: 0, width: vw, height: Math.max(hole.y, 0) }} />
          <div className="absolute bg-black/40 pointer-events-auto" style={{ left: 0, top: hole.b, width: vw, height: Math.max(vh - hole.b, 0) }} />
          <div className="absolute bg-black/40 pointer-events-auto" style={{ left: 0, top: hole.y, width: Math.max(hole.x, 0), height: hole.b - hole.y }} />
          <div className="absolute bg-black/40 pointer-events-auto" style={{ left: hole.r, top: hole.y, width: Math.max(vw - hole.r, 0), height: hole.b - hole.y }} />
          <div
            className="absolute rounded-md ring-2 ring-indigo-500 animate-pulse"
            style={{ left: hole.x, top: hole.y, width: hole.r - hole.x, height: hole.b - hole.y }}
          />
        </>
      ) : (
        <div className="absolute inset-0 bg-black/40 pointer-events-auto" />
      )}
      <div
        role="dialog"
        aria-label={t('aiSetup.guide.title')}
        className="absolute pointer-events-auto bg-white rounded-xl shadow-2xl border border-gray-200 p-5"
        style={{ left, top, bottom, width: BUBBLE_W }}
      >
        <h3 className="text-base font-semibold text-gray-900">{t('aiSetup.guide.title')}</h3>
        <p className="text-sm leading-relaxed text-gray-600 mt-1.5">{t('aiSetup.guide.body')}</p>
        <a
          href={aiDocsUrl(i18n.language)}
          target="_blank"
          rel="noopener noreferrer"
          onClick={guide.docsOpened}
          className="inline-block text-sm text-indigo-600 hover:text-indigo-800 mt-2"
        >
          {t('aiSetup.guide.docs')} ↗
        </a>
        <div className="flex justify-end mt-3">
          <button onClick={() => close('later')} className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700">
            {t('aiSetup.guide.gotIt')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

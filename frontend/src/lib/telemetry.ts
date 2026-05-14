import posthog from 'posthog-js'

const POSTHOG_PROJECT_KEY = 'phc_NP0EO5Koq1dWqXEHwR14Po7bVqqtAdWINXiWypKU6H7'
const POSTHOG_HOST = 'https://t.vibe-sellers.com'
const INSTALL_ID_LS_KEY = 'vibe_seller.install_id'

let _initialized = false
let _enabled = false

// ─── Path normalization ─────────────────────────────────────────

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function normalizePath(pathname: string): string {
  return pathname
    .split('/')
    .map((seg) => (UUID_RE.test(seg) ? '[id]' : seg))
    .join('/')
}

// ─── Autocapture enrichment (ported from feature-plugin) ────────
// Walks $elements_chain to find a meaningful interactive element and
// promote its label so PostHog UI shows readable text instead of
// "clicked button" / "clicked svg".

const INTERACTIVE_TAGS = new Set([
  'a', 'button', 'input', 'select', 'textarea', 'label',
])

const INTERACTIVE_CLASS_PATTERN =
  /\b(clickable|btn|button|toggle|pill|tab|switch|chip)\b/i

interface ParsedElement {
  tag_name: string
  text: string
  attrs: Record<string, string>
}

function parseChainComponent(component: string): ParsedElement | null {
  if (!component) return null
  const tagMatch = component.match(/^([a-z][a-z0-9]*)/i)
  const tag_name = tagMatch ? tagMatch[1] : ''
  const attrs: Record<string, string> = {}
  const attrRe = /attr__([a-z_-]+)="([^"]*)"/gi
  let m: RegExpExecArray | null
  while ((m = attrRe.exec(component)) !== null) {
    attrs['attr__' + m[1]] = m[2]
  }
  const textMatch = component.match(/(?:^|[;:"])text="([^"]*)"/)
  const text = textMatch ? textMatch[1] : ''
  return { tag_name, text, attrs }
}

function hasInteractivitySignal(el: ParsedElement): boolean {
  if (
    el.attrs.attr__title ||
    el.attrs.attr__aria_label ||
    el.attrs['attr__aria-label'] ||
    el.attrs.attr__role
  ) {
    return true
  }
  const cls = el.attrs.attr__class || ''
  return INTERACTIVE_CLASS_PATTERN.test(cls)
}

function extractInteractiveClassName(el: ParsedElement): string {
  const cls = el.attrs.attr__class || ''
  const match = cls.match(
    /\b(?:clickable|btn|button|toggle|pill|tab|switch|chip)[-_]?([\w-]*)/i,
  )
  if (match) {
    return match[0].replace(/[-_]/g, ' ').trim()
  }
  return ''
}

function extractIconLabel(el: ParsedElement): string {
  const cls = el.attrs.attr__class || ''
  const lucide = cls.match(/\blucide-([a-z][-a-z0-9]*)/i)
  if (lucide) return lucide[1].replace(/-/g, ' ')
  const icon = cls.match(/\b(?:icon|fa)-([a-z][-a-z0-9]*)/i)
  if (icon) return icon[1].replace(/-/g, ' ')
  return ''
}

function extractLabel(el: ParsedElement): string {
  return (
    el.text ||
    el.attrs.attr__title ||
    el.attrs.attr__aria_label ||
    el.attrs['attr__aria-label'] ||
    el.attrs.attr__placeholder ||
    el.attrs.attr__name ||
    el.attrs.attr__id ||
    extractInteractiveClassName(el) ||
    ''
  )
}

function extractLabelFromChildren(
  components: ParsedElement[],
  interactiveIdx: number,
): string {
  for (let i = 0; i < interactiveIdx; i++) {
    const child = components[i]
    const label = extractLabel(child)
    if (label) return label
    const iconLabel = extractIconLabel(child)
    if (iconLabel) return iconLabel
  }
  return ''
}

function enrichAutocaptureEvent(
  event: Record<string, unknown>,
): Record<string, unknown> {
  if (!event || event.event !== '$autocapture') return event
  const props = event.properties as Record<string, unknown> | undefined
  const chain = props?.$elements_chain
  if (typeof chain !== 'string' || chain.length === 0) return event

  const components = chain
    .split(';')
    .map(parseChainComponent)
    .filter(Boolean) as ParsedElement[]
  if (components.length === 0) return event

  // Pass 1: standard interactive tag (button, a, input, ...).
  for (let i = 0; i < components.length; i++) {
    const el = components[i]
    if (INTERACTIVE_TAGS.has(el.tag_name)) {
      props!.action_element = el.tag_name
      props!.action_label =
        extractLabel(el) ||
        (props!.$el_text as string) ||
        extractLabelFromChildren(components, i) ||
        ''
      if (props!.action_label && props!.action_label !== props!.$el_text) {
        props!.$el_text = props!.action_label
      }
      return event
    }
  }

  // Pass 2: element with interactivity signal (class / aria / role / title).
  for (let i = 0; i < components.length; i++) {
    const el = components[i]
    if (hasInteractivitySignal(el)) {
      props!.action_element = el.tag_name
      props!.action_label =
        extractLabel(el) ||
        (props!.$el_text as string) ||
        extractLabelFromChildren(components, i) ||
        ''
      if (props!.action_label && props!.action_label !== props!.$el_text) {
        props!.$el_text = props!.action_label
      }
      return event
    }
  }

  // Pass 3: fall back to the click target itself.
  const target = components[0]
  props!.action_element = target.tag_name
  props!.action_label =
    extractLabel(target) || (props!.$el_text as string) || ''
  if (props!.action_label && props!.action_label !== props!.$el_text) {
    props!.$el_text = props!.action_label
  }
  return event
}

// ─── Init / opt-out ─────────────────────────────────────────────

function ensureInstallId(serverInstallId: string | null): string {
  if (serverInstallId) {
    try {
      localStorage.setItem(INSTALL_ID_LS_KEY, serverInstallId)
    } catch {
      // ignore
    }
    return serverInstallId
  }
  try {
    const cached = localStorage.getItem(INSTALL_ID_LS_KEY)
    if (cached) return cached
  } catch {
    // ignore
  }
  const fallback =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `anon-${Date.now()}-${Math.random().toString(36).slice(2)}`
  try {
    localStorage.setItem(INSTALL_ID_LS_KEY, fallback)
  } catch {
    // ignore
  }
  return fallback
}

export function initTelemetry(serverInstallId: string | null): void {
  if (_initialized) return
  _initialized = true
  if (typeof window === 'undefined') return
  try {
    const distinctId = ensureInstallId(serverInstallId)
    posthog.init(POSTHOG_PROJECT_KEY, {
      api_host: POSTHOG_HOST,
      autocapture: true,
      capture_pageview: true,
      capture_pageleave: true,
      persistence: 'localStorage',
      disable_session_recording: true,
      disable_external_dependency_loading: true,
      bootstrap: { distinctID: distinctId },
      before_send: (event) => {
        try {
          const props = event?.properties as
            | Record<string, unknown>
            | undefined
          if (props && typeof props.$current_url === 'string') {
            const url = new URL(props.$current_url)
            url.pathname = normalizePath(url.pathname)
            props.$current_url = url.toString()
          }
          if (props && typeof props.$pathname === 'string') {
            props.$pathname = normalizePath(props.$pathname)
          }
          enrichAutocaptureEvent(
            event as unknown as Record<string, unknown>,
          )
          return event
        } catch {
          return event
        }
      },
    })
    _enabled = true
  } catch {
    _enabled = false
  }
}

export function optOutTelemetry(): void {
  if (!_initialized) return
  try {
    posthog.opt_out_capturing()
  } catch {
    // ignore
  }
  _enabled = false
}

export function telemetryEnabled(): boolean {
  return _enabled
}

// ─── Custom events ──────────────────────────────────────────────

export function sendEvent(name: string, props: Record<string, unknown> = {}) {
  if (!_enabled) return
  try {
    posthog.capture(name, props)
  } catch {
    // ignore
  }
}

// ─── Bucket helpers (mirror app/telemetry.py) ───────────────────

export function lengthBucket(n: number): string {
  if (n <= 0) return '0'
  if (n < 50) return '<50'
  if (n < 200) return '50-200'
  if (n < 1000) return '200-1k'
  if (n < 5000) return '1k-5k'
  return '5k+'
}

export function durationBucket(ms: number): string {
  const secs = Math.max(0, Math.floor(ms / 1000))
  if (secs < 30) return '<30s'
  if (secs < 120) return '30s-2m'
  if (secs < 600) return '2m-10m'
  if (secs < 1800) return '10m-30m'
  return '>30m'
}

export function ageBucket(createdAt: string | undefined): string {
  if (!createdAt) return 'unknown'
  const t = Date.parse(createdAt)
  if (Number.isNaN(t)) return 'unknown'
  const ageS = (Date.now() - t) / 1000
  if (ageS < 60) return '<1m'
  if (ageS < 3600) return '<1h'
  if (ageS < 86400) return '<1d'
  if (ageS < 604800) return '<1w'
  return '>1w'
}

import '@testing-library/jest-dom'
import { vi } from 'vitest'

// jsdom in this config ships a localStorage shell without
// functional getItem/setItem. The app's i18n module reads from
// it at import time (via the `ui` barrel → LanguageSwitcher).
// Install a real in-memory implementation before any app code
// is loaded by a test file.
{
  const store = new Map<string, string>()
  // Deterministic test language. The app's detectLanguage() consults a
  // saved choice first; without this pin, a CI/dev host in a
  // mainland-China time zone (the third detection branch) would flip
  // every English-asserting test to Chinese.
  store.set('vibe-seller-language', 'en')
  // Steady-state precondition for the whole suite: the first-run
  // guide is already dismissed in this browser. Guide tests clear
  // this key themselves.
  store.set('vibe-seller-guide-seen', '2026-01-01T00:00:00+00:00')
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v)
      },
      removeItem: (k: string) => {
        store.delete(k)
      },
      clear: () => {
        store.clear()
      },
      key: (i: number) => Array.from(store.keys())[i] ?? null,
      get length() {
        return store.size
      },
    },
    configurable: true,
  })
}

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// Mock ResizeObserver. Constructor mocks need a `function`
// implementation: vitest 3+ mocks keep `new` semantics, and an arrow
// function is not constructible.
globalThis.ResizeObserver = vi.fn().mockImplementation(function () {
  return {
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  }
})

// Mock Element.scrollIntoView (not available in jsdom)
Element.prototype.scrollIntoView = vi.fn()

// Mock IntersectionObserver
globalThis.IntersectionObserver = vi.fn().mockImplementation(function () {
  return {
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  }
})

// Mock EventSource for SSE tests
if (!globalThis.EventSource) {
  globalThis.EventSource = vi.fn().mockImplementation(function () {
    return {
      close: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      onmessage: null,
      onerror: null,
      onopen: null,
    }
  }) as unknown as typeof EventSource
}

// "Has this browser dismissed guide X?" — shared by the first-run
// guide (useOnboardingGuide) and the AI setup guide (useAiSetupGuide).
//
// One localStorage key per guide, per browser (not per user, not on
// the server). Every access is guarded: private mode, a full quota or
// blocked site data must never break the page.

/** true = seen, false = not, null = storage threw. */
export function readSeen(key: string): boolean | null {
  try {
    return !!localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeSeen(key: string): void {
  try {
    localStorage.setItem(key, new Date().toISOString())
  } catch {
    // Storage unavailable: callers keep an in-memory guard so the
    // page load stays quiet anyway.
  }
}

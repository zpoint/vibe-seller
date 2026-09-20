import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import './index.css'
import './i18n'
import { router } from './router'
import { initTelemetry } from './lib/telemetry'
import { initUploadLimits } from './uploadLimits'

async function bootstrapTelemetry() {
  try {
    const res = await fetch('/api/settings', { credentials: 'include' })
    if (!res.ok) return
    const settings: Record<string, string> = await res.json()
    // Server-defined upload cap; the attachment dialog reads it from
    // here rather than repeating the number. Must run before the
    // telemetry opt-out check below, which returns early.
    initUploadLimits(settings)
    if (settings.telemetry_enabled === 'false') return
    initTelemetry(settings.install_id || null)
  } catch {
    // Telemetry must never block the app from rendering.
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
bootstrapTelemetry()

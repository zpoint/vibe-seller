import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import './index.css'
import i18n from './i18n'
import { router } from './router'
import { initTelemetry } from './lib/telemetry'
import { initUploadLimits } from './uploadLimits'

async function bootstrapTelemetry() {
  try {
    const [settingsRes, infoRes] = await Promise.all([
      fetch('/api/settings', { credentials: 'include' }),
      fetch('/api/system/info', { credentials: 'include' }),
    ])
    if (!settingsRes.ok) return
    const settings: Record<string, string> = await settingsRes.json()
    // Server-defined upload cap; the attachment dialog reads it from
    // here rather than repeating the number. Must run before the
    // telemetry opt-out check below, which returns early.
    initUploadLimits(settings)
    if (settings.telemetry_enabled === 'false') return
    const superProps: Record<string, string> = {
      app_locale: i18n.language,
    }
    if (infoRes.ok) {
      const info = await infoRes.json()
      if (info.version) superProps.app_version = String(info.version)
      if (info.release_channel) {
        superProps.release_channel = String(info.release_channel)
      }
      if (info.platform) superProps.runtime_os = String(info.platform)
    }
    initTelemetry(settings.install_id || null, superProps)
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

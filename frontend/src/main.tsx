import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n'
import App from './App.tsx'
import { initTelemetry } from './lib/telemetry'

async function bootstrapTelemetry() {
  try {
    const res = await fetch('/api/settings', { credentials: 'include' })
    if (!res.ok) return
    const settings: Record<string, string> = await res.json()
    if (settings.telemetry_enabled === 'false') return
    initTelemetry(settings.install_id || null)
  } catch {
    // Telemetry must never block the app from rendering.
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
bootstrapTelemetry()

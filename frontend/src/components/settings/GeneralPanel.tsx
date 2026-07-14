import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import { optOutTelemetry, sendEvent } from '../../lib/telemetry'
import { FrontendEvent } from '../../lib/telemetryEvents'
import { TimezoneSelect } from '../TimezoneSelect'
import type { AuthUser } from '../../types'

interface GeneralPanelProps {
  currentUser: AuthUser
  setCurrentUser: React.Dispatch<React.SetStateAction<AuthUser | null>>
}

export function GeneralPanel({ currentUser, setCurrentUser }: GeneralPanelProps) {
  const { t } = useTranslation()
  const [maxConcurrency, setMaxConcurrency] = useState<number>(2)
  const [taskRetentionDays, setTaskRetentionDays] = useState<number>(30)
  const [defaultPhaseMode, setDefaultPhaseMode] = useState<'fanout' | 'single'>('fanout')
  const browserZone =
    (typeof Intl !== 'undefined' &&
      Intl.DateTimeFormat().resolvedOptions().timeZone) ||
    'UTC'
  const [defaultTimezone, setDefaultTimezone] = useState<string>(browserZone)
  const [telemetryEnabled, setTelemetryEnabled] = useState<boolean>(true)
  const [browserHeadless, setBrowserHeadless] = useState<boolean>(false)
  const [skillsAutoSync, setSkillsAutoSync] = useState<boolean>(true)

  useEffect(() => {
    api.get('/api/settings').then((s: Record<string, string>) => {
      if (s.max_agent_concurrency) {
        setMaxConcurrency(parseInt(s.max_agent_concurrency, 10) || 2)
      }
      if (s.telemetry_enabled !== undefined) {
        setTelemetryEnabled(s.telemetry_enabled !== 'false')
      }
      if (s.browser_headless !== undefined) {
        setBrowserHeadless(s.browser_headless === 'true')
      }
      if (s.default_schedule_phase_mode === 'fanout' || s.default_schedule_phase_mode === 'single') {
        setDefaultPhaseMode(s.default_schedule_phase_mode)
      }
      if (s.default_schedule_timezone) {
        setDefaultTimezone(s.default_schedule_timezone)
      }
      if (s.task_retention_days !== undefined) {
        const n = parseInt(s.task_retention_days, 10)
        if (!Number.isNaN(n)) setTaskRetentionDays(n)
      }
      if (s.skills_auto_sync_enabled !== undefined) {
        setSkillsAutoSync(s.skills_auto_sync_enabled !== 'false')
      }
    }).catch(() => {})
  }, [])

  const saveConcurrency = async (val: number) => {
    const clamped = Math.max(1, Math.min(10, val))
    setMaxConcurrency(clamped)
    try { await api.put('/api/settings', { max_agent_concurrency: clamped }) } catch { /* ignore */ }
  }

  const saveDefaultPhaseMode = async (val: 'fanout' | 'single') => {
    setDefaultPhaseMode(val)
    try { await api.put('/api/settings', { default_schedule_phase_mode: val }) } catch { /* ignore */ }
  }

  const saveDefaultTimezone = async (tz: string) => {
    setDefaultTimezone(tz)
    try { await api.put('/api/settings', { default_schedule_timezone: tz }) } catch { /* ignore */ }
  }

  const saveTaskRetention = async (val: number) => {
    const clamped = Math.max(0, Math.min(3650, Number.isFinite(val) ? val : 30))
    setTaskRetentionDays(clamped)
    try { await api.put('/api/settings', { task_retention_days: clamped }) } catch { /* ignore */ }
  }

  const saveTelemetry = async (next: boolean) => {
    sendEvent(FrontendEvent.SETTINGS_TELEMETRY_TOGGLED, { enabled: next })
    setTelemetryEnabled(next)
    try { await api.put('/api/settings', { telemetry_enabled: next }) } catch { /* ignore */ }
    if (!next) {
      optOutTelemetry()
    } else {
      window.location.reload()
    }
  }

  const saveSkillsAutoSync = async (next: boolean) => {
    // Optimistic flip + revert on failure. Non-admin users get a
    // 403 from PUT /api/settings; without the revert the toggle
    // would visually change but the backend wouldn't, which is
    // worse than just refusing to move.
    const prev = skillsAutoSync
    setSkillsAutoSync(next)
    try {
      await api.put('/api/settings', { skills_auto_sync_enabled: next })
    } catch {
      setSkillsAutoSync(prev)
    }
  }

  const saveBrowserHeadless = async (next: boolean) => {
    if (next !== browserHeadless) {
      const confirmed = window.confirm(t('settings.browserHeadlessWarn'))
      if (!confirmed) return
    }
    setBrowserHeadless(next)
    try { await api.put('/api/settings', { browser_headless: next }) } catch { /* ignore */ }
  }

  const setDefaultPlanMode = async (newVal: boolean) => {
    if (newVal === currentUser.plan_mode_default) return
    try {
      await api.patch('/api/auth/me/profile', { plan_mode_default: newVal })
      setCurrentUser(prev => prev ? { ...prev, plan_mode_default: newVal } : prev)
    } catch { /* ignore */ }
  }

  const planMode = currentUser.plan_mode_default
  const planModeDesc = planMode ? t('tasks.modePlanDesc') : t('tasks.modeAutoDesc')

  return (
    <div className="space-y-4">
      {/* Task defaults */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold text-sm mb-3">{t('settings.taskDefaultsTitle')}</h3>
        <div className="space-y-3">
          {/* Default execution mode (segmented) */}
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">{t('settings.defaultExecutionMode')}</p>
              <p className="text-xs text-gray-500">{planModeDesc}</p>
            </div>
            <div className="inline-flex items-center rounded-md border border-gray-200 bg-white p-0.5">
              <button
                type="button"
                aria-pressed={!planMode}
                onClick={() => setDefaultPlanMode(false)}
                className={`px-2.5 py-1 text-xs rounded transition-colors ${!planMode ? 'bg-gray-100 text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
              >
                {t('tasks.modeAuto')}
              </button>
              <button
                type="button"
                aria-pressed={planMode}
                onClick={() => setDefaultPlanMode(true)}
                className={`px-2.5 py-1 text-xs rounded transition-colors ${planMode ? 'bg-gray-100 text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
              >
                {t('tasks.modePlan')}
              </button>
            </div>
          </div>
          {/* Task retention */}
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">{t('settings.taskRetention')}</p>
              <p className="text-xs text-gray-500">{t('settings.taskRetentionDesc')}</p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={3650}
                value={taskRetentionDays}
                onChange={e => {
                  const n = parseInt(e.target.value, 10)
                  if (!Number.isNaN(n)) setTaskRetentionDays(n)
                }}
                onBlur={e => {
                  const n = parseInt(e.target.value, 10)
                  if (!Number.isNaN(n)) saveTaskRetention(n)
                  else saveTaskRetention(taskRetentionDays)
                }}
                className="w-20 px-2 py-1 text-sm border border-gray-300 rounded text-right"
              />
              <span className="text-xs text-gray-500">
                {taskRetentionDays === 0 ? t('settings.taskRetentionDisabled') : t('settings.taskRetentionDays')}
              </span>
            </div>
          </div>
          {/* Max concurrency */}
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
            <div>
              <p className="text-sm font-medium">{t('settings.maxConcurrency')}</p>
              <p className="text-xs text-gray-500">{t('settings.maxConcurrencyDesc')}</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => saveConcurrency(maxConcurrency - 1)}
                disabled={maxConcurrency <= 1}
                className="w-7 h-7 rounded border border-gray-300 text-sm font-medium hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
              >−</button>
              <span className="w-8 text-center text-sm font-medium">{maxConcurrency}</span>
              <button
                onClick={() => saveConcurrency(maxConcurrency + 1)}
                disabled={maxConcurrency >= 10}
                className="w-7 h-7 rounded border border-gray-300 text-sm font-medium hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
              >+</button>
            </div>
          </div>
        </div>
      </div>

      {/* Schedule defaults */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold text-sm mb-3">{t('settings.scheduleDefaultsTitle')}</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">{t('settings.defaultScheduleTimezone')}</p>
              <p className="text-xs text-gray-500">{t('settings.defaultScheduleTimezoneDesc')}</p>
            </div>
            <div className="w-64">
              <TimezoneSelect
                value={defaultTimezone}
                onChange={saveDefaultTimezone}
              />
            </div>
          </div>
          <fieldset className="p-3 bg-gray-50 rounded-lg space-y-2">
            <legend className="mb-1">
              <p className="text-sm font-medium mb-0.5">{t('schedules.defaultMode')}</p>
              <p className="text-xs text-gray-500">{t('schedules.defaultModeHelp')}</p>
            </legend>
            <label className="flex items-start gap-2 p-2 border border-gray-200 bg-white rounded-lg cursor-pointer hover:bg-gray-50">
              <input
                type="radio"
                name="default-schedule-phase-mode"
                className="mt-1"
                checked={defaultPhaseMode === 'fanout'}
                onChange={() => saveDefaultPhaseMode('fanout')}
              />
              <div className="flex-1">
                <div className="text-sm font-medium text-gray-900">{t('schedules.fanoutMode')}</div>
                <div className="text-xs text-gray-500">{t('schedules.fanoutModeDescription')}</div>
              </div>
            </label>
            <label className="flex items-start gap-2 p-2 border border-gray-200 bg-white rounded-lg cursor-pointer hover:bg-gray-50">
              <input
                type="radio"
                name="default-schedule-phase-mode"
                className="mt-1"
                checked={defaultPhaseMode === 'single'}
                onChange={() => saveDefaultPhaseMode('single')}
              />
              <div className="flex-1">
                <div className="text-sm font-medium text-gray-900">{t('schedules.singleMode')}</div>
                <div className="text-xs text-gray-500">{t('schedules.singleModeDescription')}</div>
              </div>
            </label>
          </fieldset>
        </div>
      </div>

      {/* Browser */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold text-sm mb-3">{t('settings.browserTitle')}</h3>
        <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">{t('settings.browserHeadlessTitle')}</p>
            <p className="text-xs text-gray-500">{t('settings.browserHeadlessDesc')}</p>
          </div>
          <label className="inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              className="sr-only peer"
              checked={browserHeadless}
              onChange={e => saveBrowserHeadless(e.target.checked)}
            />
            <span className="w-10 h-5 bg-gray-300 rounded-full relative transition-colors peer-checked:bg-indigo-600">
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${browserHeadless ? 'translate-x-5' : ''}`}></span>
            </span>
          </label>
        </div>
      </div>

      {/* Skills */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold text-sm mb-3">{t('settings.skillsTitle')}</h3>
        <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">{t('settings.skillsAutoSyncTitle')}</p>
            <p className="text-xs text-gray-500">{t('settings.skillsAutoSyncDesc')}</p>
          </div>
          <label className="inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              className="sr-only peer"
              checked={skillsAutoSync}
              onChange={e => saveSkillsAutoSync(e.target.checked)}
              aria-label={t('settings.skillsAutoSyncTitle')}
            />
            <span className="w-10 h-5 bg-gray-300 rounded-full relative transition-colors peer-checked:bg-indigo-600">
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${skillsAutoSync ? 'translate-x-5' : ''}`}></span>
            </span>
          </label>
        </div>
      </div>

      {/* Privacy */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold text-sm mb-3">{t('settings.privacyTitle')}</h3>
        <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">{t('settings.telemetryTitle')}</p>
            <p className="text-xs text-gray-500">{t('settings.telemetryDesc')}</p>
          </div>
          <label className="inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              className="sr-only peer"
              checked={telemetryEnabled}
              onChange={e => saveTelemetry(e.target.checked)}
            />
            <span className="w-10 h-5 bg-gray-300 rounded-full relative transition-colors peer-checked:bg-indigo-600">
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${telemetryEnabled ? 'translate-x-5' : ''}`}></span>
            </span>
          </label>
        </div>
      </div>
    </div>
  )
}

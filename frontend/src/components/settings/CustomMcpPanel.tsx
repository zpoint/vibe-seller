import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import { LONG_TIMEOUT_MS } from '../../lib/requestDeadline'
import { DocsHelpLink } from '../ui/DocsHelpLink'
import { MCP_CONFIG_ERROR_HELP, MCP_REASON_HELP, type DocsHelpTopic } from '../../lib/docsHelp'

// One external MCP server an admin added for every task's agent
// (backend: app/routers/custom_mcp.py). Credentials arrive masked —
// the server never sends them back.
export interface CustomMcpServer {
  id: string
  /** The key tool names are built from (`mcp__<name>__…`). */
  name: string
  /** What people call it — 「西柚MCP」; the key when none was typed. */
  label: string
  type: 'http' | 'sse' | 'stdio' | 'unknown'
  url?: string
  headers?: Record<string, string>
  command?: string
  args?: string[]
  env?: Record<string, string>
  enabled: boolean
  valid: boolean
  problem: string | null
}

export interface McpTestResult {
  ok: boolean
  error?: string
  reason?: string
  server_name?: string
  server_version?: string
  tool_count?: number
  tools?: { name: string; description: string }[]
}

const LIST_URL = '/api/custom-mcp-servers'

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export function CustomMcpPanel({ isAdmin = false }: { isAdmin?: boolean }) {
  const { t } = useTranslation()
  const [servers, setServers] = useState<CustomMcpServer[]>([])
  const [adding, setAdding] = useState(false)
  const [config, setConfig] = useState('')
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setErrorText] = useState('')
  const [errorHelp, setErrorHelp] = useState<DocsHelpTopic | null>(null)
  /** Show *text* (nothing when empty), with the docs entry that explains it. */
  const setError = (text: string, help: DocsHelpTopic | null = null) => {
    setErrorText(text)
    setErrorHelp(help)
  }
  const [tests, setTests] = useState<Record<string, McpTestResult | 'running'>>({})

  const refresh = useCallback(async () => {
    try {
      setServers(await api.get(LIST_URL))
    } catch { /* a failed list keeps the last one */ }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const runTest = useCallback(async (id: string) => {
    setTests(prev => ({ ...prev, [id]: 'running' }))
    try {
      // A local server may download its package on first start.
      const res: McpTestResult = await api.post(
        `${LIST_URL}/${id}/test`, undefined, { timeoutMs: LONG_TIMEOUT_MS },
      )
      setTests(prev => ({ ...prev, [id]: res }))
    } catch (e) {
      setTests(prev => ({ ...prev, [id]: { ok: false, error: errorText(e) } }))
    }
  }, [])

  const save = async (replace = false) => {
    if (!config.trim()) {
      setError(t('customMcp.configRequired'))
      return
    }
    if (!name.trim()) {
      setError(t('customMcp.nameRequired'))
      return
    }
    setSaving(true)
    setError('')
    try {
      // The server connects to each server before saving it (like the
      // AI-profile save probe) and answers with what it found.
      const saved: (CustomMcpServer & { test?: McpTestResult })[] = await api.post(
        LIST_URL,
        { config, name: name.trim() || null, replace },
        { timeoutMs: LONG_TIMEOUT_MS },
      )
      setAdding(false)
      setConfig('')
      setName('')
      setTests(prev => {
        const next = { ...prev }
        for (const s of saved) if (s.test) next[s.id] = s.test
        return next
      })
      await refresh()
    } catch (e) {
      const err = e as Error & {
        status?: number
        detail?: {
          names?: string[]; labels?: string[]; code?: string; name?: string; label?: string
          error?: string; reason?: string; message?: string
        }
      }
      const code = err.detail?.code ?? ''
      if (code === 'connect_failed') {
        setError(t('customMcp.connectFailed', {
          name: err.detail?.label ?? err.detail?.name,
          why: reasonText(t, err.detail?.reason, err.detail?.error),
        }), MCP_REASON_HELP[err.detail?.reason ?? ''] ?? null)
      } else if (code.startsWith('config_')) {
        // Say the common mistakes in the user's language; anything else
        // falls back to the server's own sentence.
        setError(t(`customMcp.configErrors.${code.slice('config_'.length)}`, {
          ...err.detail,
          defaultValue: err.detail?.message ?? err.message,
        }), MCP_CONFIG_ERROR_HELP[code] ?? null)
      } else if (err.status === 409 && err.detail?.names) {
        const names = (err.detail.labels ?? err.detail.names).join(', ')
        if (confirm(t('customMcp.replaceConfirm', { names }))) {
          setSaving(false)
          await save(true)
          return
        }
      } else {
        setError(errorText(e))
      }
    }
    setSaving(false)
  }

  const toggle = async (s: CustomMcpServer) => {
    try {
      await api.patch(`${LIST_URL}/${s.id}`, { enabled: !s.enabled })
      await refresh()
    } catch (e) {
      setError(errorText(e))
    }
  }

  const remove = async (s: CustomMcpServer) => {
    if (!confirm(t('customMcp.deleteConfirm', { name: s.label }))) return
    try {
      await api.del(`${LIST_URL}/${s.id}`)
      await refresh()
    } catch (e) {
      setError(errorText(e))
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4" data-testid="custom-mcp-card">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-semibold">{t('customMcp.title')}</h3>
        {isAdmin && !adding && (
          <button
            type="button"
            data-testid="custom-mcp-add"
            onClick={() => { setAdding(true); setError('') }}
            className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 shrink-0"
          >
            {t('customMcp.add')}
          </button>
        )}
      </div>
      <p className="text-sm text-gray-500 mt-1">{t('customMcp.description')}</p>

      {error && (
        <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700" data-testid="custom-mcp-error">
          {error}
          {errorHelp && <> <DocsHelpLink topic={errorHelp} /></>}
          <button onClick={() => setError('')} className="ml-2 text-red-500 hover:text-red-700">&times;</button>
        </div>
      )}

      {adding && (
        <div className="mt-3 space-y-3 border border-gray-200 rounded-lg p-3" data-testid="custom-mcp-form">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1" htmlFor="custom-mcp-config">
              {t('customMcp.configLabel')}
            </label>
            <p className="text-xs text-gray-400 mb-1">{t('customMcp.configHint')}</p>
            <textarea
              id="custom-mcp-config"
              data-testid="custom-mcp-config"
              value={config}
              onChange={e => setConfig(e.target.value)}
              rows={11}
              spellCheck={false}
              placeholder={'{\n  "mcpServers": {\n    "my-service": {\n      "url": "https://example.com/mcp",\n      "headers": { "Authorization": "Bearer <token>" }\n    }\n  }\n}'}
              className="w-full px-3 py-2 border border-gray-300 rounded text-xs font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1" htmlFor="custom-mcp-name">
              {t('customMcp.nameLabel')}
            </label>
            <input
              id="custom-mcp-name"
              data-testid="custom-mcp-name"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder={t('customMcp.namePlaceholder')}
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm font-mono"
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="custom-mcp-save"
              onClick={() => save()}
              disabled={saving}
              className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? t('customMcp.connecting') : t('common.save')}
            </button>
            <button
              type="button"
              onClick={() => { setAdding(false); setError('') }}
              className="px-3 py-1.5 border border-gray-300 rounded text-sm hover:bg-gray-50"
            >
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}

      <div className="mt-3 space-y-2">
        {servers.length === 0 && !adding && (
          <p className="text-sm text-gray-400" data-testid="custom-mcp-empty">{t('customMcp.empty')}</p>
        )}
        {servers.map(s => (
          <ServerRow
            key={s.id}
            server={s}
            isAdmin={isAdmin}
            test={tests[s.id]}
            onTest={() => runTest(s.id)}
            onToggle={() => toggle(s)}
            onDelete={() => remove(s)}
          />
        ))}
      </div>

      {servers.length > 0 && (
        <p className="text-xs text-gray-400 mt-3">{t('customMcp.appliesNote')}</p>
      )}
      {!isAdmin && (
        <p className="text-xs text-gray-400 mt-2">{t('customMcp.adminOnly')}</p>
      )}
    </div>
  )
}

/** Why a connection failed, in the user's language when we know the case. */
function reasonText(
  t: (k: string, o?: Record<string, unknown>) => string,
  reason: string | undefined,
  error: string | undefined,
): string {
  return t(`customMcp.reasons.${reason ?? 'other'}`, { defaultValue: error ?? '' })
}

/** The docs FAQ entry for a connection that failed for *reason*. */
function ReasonHelp({ reason }: { reason: string | undefined }) {
  const topic = reason ? MCP_REASON_HELP[reason] : undefined
  return topic ? <> <DocsHelpLink topic={topic} /></> : null
}

function typeLabel(type: CustomMcpServer['type'], t: (k: string) => string): string {
  if (type === 'stdio') return t('customMcp.typeStdio')
  if (type === 'sse') return 'SSE'
  if (type === 'http') return 'HTTP'
  return '?'
}

function ServerRow({
  server: s,
  isAdmin,
  test,
  onTest,
  onToggle,
  onDelete,
}: {
  server: CustomMcpServer
  isAdmin: boolean
  test: McpTestResult | 'running' | undefined
  onTest: () => void
  onToggle: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const [showTools, setShowTools] = useState(false)
  const target = s.type === 'stdio'
    ? [s.command, ...(s.args ?? [])].join(' ')
    : s.url
  const secretNames = Object.keys(s.type === 'stdio' ? s.env ?? {} : s.headers ?? {})

  return (
    <div
      className={`border rounded-lg p-3 ${s.enabled ? 'border-gray-200' : 'border-gray-200 bg-gray-50'}`}
      data-testid={`custom-mcp-row-${s.name}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{s.label}</span>
            {s.label !== s.name && (
              <span className="font-mono text-xs text-gray-400" title={t('customMcp.keyHint')}>{s.name}</span>
            )}
            <span className="px-1.5 py-0.5 text-[11px] bg-gray-100 text-gray-600 rounded">
              {typeLabel(s.type, t)}
            </span>
            <span
              className={`px-1.5 py-0.5 text-[11px] rounded ${s.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-200 text-gray-600'}`}
              data-testid="custom-mcp-state"
            >
              {s.enabled ? t('customMcp.enabled') : t('customMcp.disabled')}
            </span>
          </div>
          {target && (
            <p className="text-xs text-gray-500 font-mono mt-1 break-all" data-testid="custom-mcp-target">{target}</p>
          )}
          {secretNames.length > 0 && (
            <p className="text-xs text-gray-400 mt-0.5">
              {s.type === 'stdio' ? t('customMcp.envVars') : t('customMcp.headers')}
              {': '}
              <span className="font-mono">
                {secretNames.map(k => `${k}: ${(s.type === 'stdio' ? s.env : s.headers)?.[k]}`).join(', ')}
              </span>
            </p>
          )}
          {!s.valid && s.problem && (
            <p className="text-xs text-amber-700 mt-1">{t('customMcp.invalid', { problem: s.problem })}</p>
          )}
        </div>
        {isAdmin && (
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              data-testid="custom-mcp-test"
              onClick={onTest}
              disabled={test === 'running'}
              className="px-2.5 py-1 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50"
            >
              {test === 'running' ? t('customMcp.testing') : t('customMcp.test')}
            </button>
            <button
              type="button"
              data-testid="custom-mcp-toggle"
              onClick={onToggle}
              className="px-2.5 py-1 border border-gray-300 rounded text-xs hover:bg-gray-50"
            >
              {s.enabled ? t('customMcp.disable') : t('customMcp.enable')}
            </button>
            <button
              type="button"
              data-testid="custom-mcp-delete"
              onClick={onDelete}
              className="px-2.5 py-1 bg-red-50 text-red-600 border border-red-300 rounded text-xs hover:bg-red-100"
            >
              {t('common.delete')}
            </button>
          </div>
        )}
      </div>
      {test && test !== 'running' && (
        <div className="mt-2 text-xs" data-testid="custom-mcp-test-result">
          {test.ok ? (
            <div className="text-green-700">
              {t('customMcp.testOk', {
                server: test.server_name || s.label,
                count: test.tool_count ?? 0,
              })}
              {(test.tools?.length ?? 0) > 0 && (
                <button
                  type="button"
                  onClick={() => setShowTools(v => !v)}
                  className="ml-2 text-indigo-600 hover:underline"
                >
                  {showTools ? t('customMcp.hideTools') : t('customMcp.showTools')}
                </button>
              )}
              {showTools && (
                <ul className="mt-1 space-y-0.5 text-gray-600 max-h-64 overflow-y-auto" data-testid="custom-mcp-tools">
                  {test.tools?.map(tool => (
                    <li key={tool.name} className="truncate" title={tool.description}>
                      <span className="font-mono">{tool.name}</span>
                      {tool.description && <span className="text-gray-400"> — {tool.description}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <div className="text-red-600">
              {t('customMcp.testFailed', { error: reasonText(t, test.reason, test.error) })}
              <ReasonHelp reason={test.reason} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

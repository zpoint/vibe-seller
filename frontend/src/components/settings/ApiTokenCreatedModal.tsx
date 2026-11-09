import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { buildSnippets, isLoopbackAddress } from '../../lib/apiTokenSnippets'
import { copyText } from '../../lib/clipboard'

type Props = {
  /** The plaintext token. Shown exactly once — the server never
   *  stores it, so there is nothing to re-fetch later. */
  token: string
  onClose: () => void
}

/** One snippet: a label, a copy button and the text itself. */
function SnippetBlock({
  id,
  label,
  hint,
  text,
  copied,
  onCopy,
}: {
  id: string
  label: string
  hint?: string
  text: string
  copied: boolean
  onCopy: (id: string, text: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="mt-4">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-gray-900">{label}</p>
        <button
          onClick={() => onCopy(id, text)}
          className="shrink-0 rounded border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {copied ? t('apiTokens.copied') : t('apiTokens.copy')}
        </button>
      </div>
      {hint && <p className="mt-0.5 text-xs text-gray-500">{hint}</p>}
      <pre
        data-testid={`api-token-snippet-${id}`}
        className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-100 p-2.5 font-mono text-xs text-gray-900"
      >
        {text}
      </pre>
    </div>
  )
}

/** Shown once, right after a token is minted: the plaintext token plus
 *  copy-paste configs for the clients that can use it. There is no
 *  second chance — the server keeps only a hash. */
export function ApiTokenCreatedModal({ token, onClose }: Props) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState<string | null>(null)
  // The server address every snippet points at. Prefilled with the
  // origin this page was served from, but editable: the agent may run
  // on a machine that reaches the server by a different address.
  const [address, setAddress] = useState(window.location.origin)
  const panelRef = useRef<HTMLDivElement>(null)

  // aria-modal only describes the dialog; it does not move focus. Take
  // focus on open, keep Tab / Shift+Tab inside, give it back on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    return () => opener?.focus?.()
  }, [])

  const trapTab = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab' || !panelRef.current) return
    const focusable = panelRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea, [href], [tabindex]:not([tabindex="-1"])',
    )
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || active === panelRef.current)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && active === last) {
      e.preventDefault()
      first.focus()
    }
  }
  const snippets = buildSnippets(token, address)
  const loopback = isLoopbackAddress(address)

  const copy = async (id: string, text: string) => {
    const ok = await copyText(text)
    if (!ok) return
    setCopied(id)
    setTimeout(() => setCopied((cur) => (cur === id ? null : cur)), 2000)
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="api-token-created-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onKeyDown={trapTab}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        data-testid="api-token-created-panel"
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-6 shadow-xl outline-none"
      >
        <h2
          id="api-token-created-title"
          className="text-lg font-semibold text-gray-900"
        >
          {t('apiTokens.createdTitle')}
        </h2>
        <p className="mt-2 text-sm text-amber-700">{t('apiTokens.createdOnce')}</p>

        <div className="mt-4">
          <label
            htmlFor="api-token-server-address"
            className="block text-sm font-medium text-gray-900"
          >
            {t('apiTokens.serverAddress')}
          </label>
          <p className="mt-0.5 text-xs text-gray-500">
            {t('apiTokens.serverAddressHint')}
          </p>
          <input
            id="api-token-server-address"
            data-testid="api-token-server-address"
            type="text"
            value={address}
            onChange={e => setAddress(e.target.value)}
            spellCheck={false}
            autoCapitalize="off"
            className="mt-1.5 w-full rounded border border-gray-300 px-3 py-2 font-mono text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          {loopback && (
            <p
              data-testid="api-token-loopback-warning"
              className="mt-2 p-2.5 rounded bg-amber-50 border border-amber-200 text-xs text-amber-800"
            >
              {t('apiTokens.loopbackWarning')}
            </p>
          )}
        </div>

        <SnippetBlock
          id="raw"
          label={t('apiTokens.rawToken')}
          hint={t('apiTokens.rawTokenHint')}
          text={snippets.raw}
          copied={copied === 'raw'}
          onCopy={copy}
        />

        <SnippetBlock
          id="claude-code"
          label={t('apiTokens.claudeCode')}
          hint={t('apiTokens.claudeCodeHint')}
          text={snippets.claudeCode}
          copied={copied === 'claude-code'}
          onCopy={copy}
        />

        <SnippetBlock
          id="codex"
          label={t('apiTokens.codex')}
          hint={t('apiTokens.codexHint')}
          text={snippets.codex}
          copied={copied === 'codex'}
          onCopy={copy}
        />

        <SnippetBlock
          id="workbuddy"
          label={t('apiTokens.workbuddy')}
          hint={t('apiTokens.workbuddyHint')}
          text={snippets.workbuddy}
          copied={copied === 'workbuddy'}
          onCopy={copy}
        />

        <SnippetBlock
          id="qoder"
          label={t('apiTokens.qoder')}
          hint={t('apiTokens.qoderHint')}
          text={snippets.qoder}
          copied={copied === 'qoder'}
          onCopy={copy}
        />

        <SnippetBlock
          id="hermes"
          label={t('apiTokens.hermes')}
          hint={t('apiTokens.hermesHint')}
          text={snippets.hermes}
          copied={copied === 'hermes'}
          onCopy={copy}
        />

        <SnippetBlock
          id="pi"
          label={t('apiTokens.pi')}
          hint={t('apiTokens.piHint')}
          text={snippets.pi}
          copied={copied === 'pi'}
          onCopy={copy}
        />

        <div className="mt-6 flex justify-end">
          <button
            onClick={onClose}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
          >
            {t('apiTokens.done')}
          </button>
        </div>
      </div>
    </div>
  )
}

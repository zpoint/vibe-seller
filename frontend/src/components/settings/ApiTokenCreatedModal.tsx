import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import {
  buildSnippets,
  isLoopbackAddress,
  type CodexFormValues,
} from '../../lib/apiTokenSnippets'
import { copyText } from '../../lib/clipboard'

type Props = {
  /** The plaintext token. Shown exactly once — the server never
   *  stores it, so there is nothing to re-fetch later. */
  token: string
  onClose: () => void
}

/** One snippet: a copy button and the text itself, led by the label
 *  or the hint that says where it goes.
 *
 *  With a `label` the block stands on its own — that is the raw API
 *  key at the top of the dialog, which still reads label row, hint
 *  underneath, then the snippet. Inside a tool card the card title
 *  already names the tool, so `hint` takes the label's place on the
 *  button row and the block reads hint → snippet. */
function SnippetBlock({
  id,
  label,
  hint,
  text,
  copied,
  onCopy,
}: {
  id: string
  label?: string
  hint?: string
  text: string
  copied: boolean
  onCopy: (id: string, text: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="mt-3">
      <div className="flex items-baseline justify-between gap-2">
        {label ? (
          <p className="text-sm font-medium text-gray-900">{label}</p>
        ) : hint ? (
          <p className="min-w-0 text-xs text-gray-500">{hint}</p>
        ) : (
          <span />
        )}
        <button
          data-testid={`api-token-copy-${id}`}
          onClick={() => onCopy(id, text)}
          className="shrink-0 rounded border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {copied ? t('apiTokens.copied') : t('apiTokens.copy')}
        </button>
      </div>
      {label && hint && <p className="mt-0.5 text-xs text-gray-500">{hint}</p>}
      <pre
        data-testid={`api-token-snippet-${id}`}
        className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-100 p-2.5 font-mono text-xs text-gray-900"
      >
        {text}
      </pre>
    </div>
  )
}

/** One field of the Codex desktop form: its label, the value to type
 *  in, and a copy button for that value alone. */
function CodexFormRow({
  id,
  label,
  value,
  copied,
  onCopy,
}: {
  id: string
  label: string
  value: string
  copied: boolean
  onCopy: (id: string, text: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex items-start justify-between gap-2 py-1">
      <p className="shrink-0 pt-1 text-xs text-gray-500">{label}</p>
      <div className="flex min-w-0 items-start justify-end gap-2">
        <code
          data-testid={`api-token-${id}`}
          className="min-w-0 break-all rounded bg-gray-100 px-2 py-1 font-mono text-xs text-gray-900"
        >
          {value}
        </code>
        <button
          data-testid={`api-token-copy-${id}`}
          onClick={() => onCopy(id, value)}
          className="shrink-0 rounded border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {copied ? t('apiTokens.copied') : t('apiTokens.copy')}
        </button>
      </div>
    </div>
  )
}

/** Codex desktop (Windows and macOS) adds this server through its
 *  settings form instead of config.toml, and the form takes ONE value
 *  per field — so this block is four copy targets, not a paste block.
 *  It is the body of the Codex card's 「界面操作」/"In the app" tab;
 *  the config.toml block lives on its 「命令行」tab. */
function CodexFormBlock({
  values,
  copied,
  onCopy,
}: {
  values: CodexFormValues
  copied: string | null
  onCopy: (id: string, text: string) => void
}) {
  const { t } = useTranslation()
  const rows = [
    {
      id: 'codex-form-name',
      label: t('apiTokens.codexFormName'),
      value: values.name,
    },
    {
      id: 'codex-form-url',
      label: t('apiTokens.codexFormUrl'),
      value: values.url,
    },
    {
      id: 'codex-form-header-name',
      label: t('apiTokens.codexFormHeaderName'),
      value: values.headerName,
    },
    {
      id: 'codex-form-header-value',
      label: t('apiTokens.codexFormHeaderValue'),
      value: values.headerValue,
    },
  ]
  return (
    <div className="mt-3">
      <p className="text-xs text-gray-500">{t('apiTokens.codexFormHint')}</p>
      <div
        data-testid="api-token-codex-form"
        className="mt-1.5 rounded border border-gray-200 px-2.5 py-0.5"
      >
        {rows.map(row => (
          <CodexFormRow
            key={row.id}
            {...row}
            copied={copied === row.id}
            onCopy={onCopy}
          />
        ))}
      </div>
    </div>
  )
}

/** A one-line note that belongs to the card rather than to a snippet:
 *  why a tool has no GUI route, a paste-time warning, or where the
 *  config file lives. */
function CardNote({
  testid,
  text,
  tone = 'plain',
}: {
  testid: string
  text: string
  tone?: 'plain' | 'warning'
}) {
  return (
    <p
      data-testid={testid}
      className={
        tone === 'warning'
          ? 'mt-2 rounded border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-800'
          : 'mt-2 text-xs text-gray-500'
      }
    >
      {text}
    </p>
  )
}

/** One route to wire a client up: what you click in its GUI ('app'),
 *  or what you paste into a terminal / config file ('cli'). */
interface ToolTab {
  id: string
  label: string
  body: ReactNode
}

/** One client in the dialog: its name, then its copy-paste recipe
 *  behind up to two tabs. The 「界面操作」/"In the app" tab comes
 *  first and is selected by default; a tool that is only wired up
 *  from a command line or a config file shows that one tab alone —
 *  an empty tab would just invite a click that goes nowhere.
 *
 *  Only the selected panel is in the DOM, so every snippet that is
 *  visible is also the one the user means to copy. Arrow keys move
 *  between tabs (the usual ARIA tablist), and each tab keeps a
 *  roving tabindex so the panel content is reached with Tab. */
function ToolCard({
  tool,
  title,
  tabs,
}: {
  tool: string
  title: string
  tabs: ToolTab[]
}) {
  const [active, setActive] = useState(0)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    const step = e.key === 'ArrowRight' ? 1 : tabs.length - 1
    const next = (active + step) % tabs.length
    setActive(next)
    tabRefs.current[next]?.focus()
  }

  const current = tabs[active]
  return (
    <section data-testid={`api-token-card-${tool}`} className="mt-6">
      <h3
        id={`api-token-card-title-${tool}`}
        className="text-sm font-medium text-gray-900"
      >
        {title}
      </h3>
      <div
        role="tablist"
        aria-labelledby={`api-token-card-title-${tool}`}
        onKeyDown={onKeyDown}
        className="mt-1.5 flex gap-1 border-b border-gray-200"
      >
        {tabs.map((tab, i) => (
          <button
            key={tab.id}
            ref={el => {
              tabRefs.current[i] = el
            }}
            role="tab"
            id={`api-token-tab-${tool}-${tab.id}`}
            data-testid={`api-token-tab-${tool}-${tab.id}`}
            aria-selected={active === i}
            aria-controls={`api-token-panel-${tool}-${tab.id}`}
            tabIndex={active === i ? 0 : -1}
            onClick={() => setActive(i)}
            className={`-mb-px rounded-t border-b-2 px-2.5 py-1.5 text-xs font-medium ${
              active === i
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`api-token-panel-${tool}-${current.id}`}
        data-testid={`api-token-panel-${tool}-${current.id}`}
        aria-labelledby={`api-token-tab-${tool}-${current.id}`}
      >
        {current.body}
      </div>
    </section>
  )
}

/** Shown once, right after a token is minted: the plaintext token plus
 *  copy-paste configs for the clients that can use it. There is no
 *  second chance — the server keeps only a hash.
 *
 *  Each client is one card with up to two tabs — the GUI route first
 *  where the client has one (Codex's form, WorkBuddy's and Qoder's
 *  connector editors, Hermes Desktop's paste box), the command-line /
 *  config-file route otherwise (Claude Code, Pi, and every client's
 *  config file). */
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

        <ToolCard
          tool="codex"
          title={t('apiTokens.codex')}
          tabs={[
            {
              id: 'app',
              label: t('apiTokens.tabApp'),
              body: (
                <CodexFormBlock
                  values={snippets.codexForm}
                  copied={copied}
                  onCopy={copy}
                />
              ),
            },
            {
              id: 'cli',
              label: t('apiTokens.tabCli'),
              body: (
                <SnippetBlock
                  id="codex"
                  hint={t('apiTokens.codexHint')}
                  text={snippets.codex}
                  copied={copied === 'codex'}
                  onCopy={copy}
                />
              ),
            },
          ]}
        />

        <ToolCard
          tool="workbuddy"
          title={t('apiTokens.workbuddy')}
          tabs={[
            {
              id: 'app',
              label: t('apiTokens.tabApp'),
              body: (
                <>
                  <SnippetBlock
                    id="workbuddy"
                    hint={t('apiTokens.workbuddyHint')}
                    text={snippets.workbuddy}
                    copied={copied === 'workbuddy'}
                    onCopy={copy}
                  />
                  <CardNote
                    testid="api-token-workbuddy-warning"
                    text={t('apiTokens.workbuddyTrustWarning')}
                    tone="warning"
                  />
                </>
              ),
            },
          ]}
        />

        <ToolCard
          tool="qoder"
          title={t('apiTokens.qoder')}
          tabs={[
            {
              id: 'app',
              label: t('apiTokens.tabApp'),
              body: (
                <>
                  <SnippetBlock
                    id="qoder"
                    hint={t('apiTokens.qoderHint')}
                    text={snippets.qoder}
                    copied={copied === 'qoder'}
                    onCopy={copy}
                  />
                  <CardNote
                    testid="api-token-qoder-file-note"
                    text={t('apiTokens.qoderFileNote')}
                  />
                </>
              ),
            },
          ]}
        />

        <ToolCard
          tool="hermes"
          title={t('apiTokens.hermes')}
          tabs={[
            {
              id: 'app',
              label: t('apiTokens.tabApp'),
              body: (
                <SnippetBlock
                  id="hermes-json"
                  hint={t('apiTokens.hermesAppHint')}
                  text={snippets.hermesJson}
                  copied={copied === 'hermes-json'}
                  onCopy={copy}
                />
              ),
            },
            {
              id: 'cli',
              label: t('apiTokens.tabCli'),
              body: (
                <SnippetBlock
                  id="hermes"
                  hint={t('apiTokens.hermesHint')}
                  text={snippets.hermes}
                  copied={copied === 'hermes'}
                  onCopy={copy}
                />
              ),
            },
          ]}
        />

        <ToolCard
          tool="pi"
          title={t('apiTokens.pi')}
          tabs={[
            {
              id: 'cli',
              label: t('apiTokens.tabCli'),
              body: (
                <SnippetBlock
                  id="pi"
                  hint={t('apiTokens.piHint')}
                  text={snippets.pi}
                  copied={copied === 'pi'}
                  onCopy={copy}
                />
              ),
            },
          ]}
        />

        <ToolCard
          tool="claude-code"
          title={t('apiTokens.claudeCode')}
          tabs={[
            {
              id: 'cli',
              label: t('apiTokens.tabCli'),
              body: (
                <SnippetBlock
                  id="claude-code"
                  hint={t('apiTokens.claudeCodeHint')}
                  text={snippets.claudeCode}
                  copied={copied === 'claude-code'}
                  onCopy={copy}
                />
              ),
            },
          ]}
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

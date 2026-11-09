/**
 * Ready-to-paste MCP client configs for an external agent, built from
 * the token the user just minted.
 *
 * The endpoint comes from a server address the user can edit in the
 * token-created dialog: it is prefilled with the page's own origin
 * (the browser is already talking to the server that serves
 * `/api/mcp`), but the prefill is only a guess — on a remote install
 * the operator may reach the server at a different address than the
 * machine the agent runs on, so the address is editable and every
 * snippet is rebuilt from it.
 *
 * The shapes match the verified Claude Code / Codex / WorkBuddy /
 * Qoder MCP config formats:
 *   - Claude Code: `claude mcp add --scope user --transport http <name>
 *                  <url> --header …` — `--scope user`, or the server
 *                  registers for the current folder only
 *   - Codex:       `~/.codex/config.toml` → `[mcp_servers.<name>]` with
 *                  `http_headers` carrying the bearer — the Codex
 *                  desktop app does not inherit shell exports, so a
 *                  `bearer_token_env_var` token fails there
 *   - WorkBuddy:   `~/.workbuddy/mcp.json` → `mcpServers.<name>.headers`
 *                  (then enable/trust it in WorkBuddy's MCP settings)
 *   - Qoder:       `~/.qoder/settings.json` → `mcpServers.<name>` with
 *                  `type: "http"`, `url` and `headers` (desktop app)
 *   - Hermes:      `~/.hermes/config.yaml` → `mcp_servers.<name>` with
 *                  `url` and `headers`. Its `hermes mcp add --auth
 *                  header` prompts for the token, so it can't be a
 *                  one-line paste; the YAML block can
 *   - Pi:          `pi mcp add <name> --url <url> --header KEY=VALUE`
 *                  (built-in MCP; writes `~/.pi/agent/mcp.json`)
 */

/** The MCP endpoint of the server this page was loaded from. */
export const mcpUrl = (): string => `${window.location.origin}/api/mcp`

/** Hostnames that only THIS machine can reach. A config pointing at
 *  them works for a local agent and silently fails for anything on
 *  another machine — worth saying out loud before the user copies it. */
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1'])

/** The hostname of *address*, or null when it is not a parseable URL.
 *  IPv6 brackets are stripped: `new URL('http://[::1]:7777')` keeps
 *  them in `hostname`, and callers want the bare `::1`. */
export const hostnameOf = (address: string): string | null => {
  try {
    const host = new URL(address).hostname
    return host.startsWith('[') && host.endsWith(']')
      ? host.slice(1, -1)
      : host
  } catch {
    return null
  }
}

/** True when *address* points at the machine itself — i.e. the copied
 *  config only works for an agent running on the same machine as the
 *  server. Unparseable addresses carry no opinion (the snippets will
 *  show the raw text; that is its own feedback). */
export const isLoopbackAddress = (address: string): boolean => {
  const host = hostnameOf(address)
  return host !== null && LOOPBACK_HOSTS.has(host)
}

export interface TokenSnippets {
  /** The raw token — send as `Authorization: Bearer <token>`. */
  raw: string
  /** A `claude mcp add …` command. */
  claudeCode: string
  /** The `~/.codex/config.toml` block. */
  codex: string
  /** The `~/.workbuddy/mcp.json` document. */
  workbuddy: string
  /** The `~/.qoder/settings.json` `mcpServers` entry (Qoder desktop). */
  qoder: string
  /** The `~/.hermes/config.yaml` `mcp_servers` block. */
  hermes: string
  /** A `pi mcp add …` command. */
  pi: string
}

/** Build every copy-paste config against *baseUrl* (the server
 *  address, no trailing slash needed — the `/api/mcp` suffix is
 *  appended here). */
export const buildSnippets = (
  token: string,
  baseUrl: string,
): TokenSnippets => {
  const root = baseUrl.trim().replace(/\/+$/, '')
  const url = `${root}/api/mcp`
  return {
    raw: token,
    claudeCode: `claude mcp add --scope user --transport http vibe-seller ${url} --header "Authorization: Bearer ${token}"`,
    codex: `[mcp_servers.vibe-seller]\nurl = "${url}"\nhttp_headers = { "Authorization" = "Bearer ${token}" }`,
    workbuddy: JSON.stringify(
      {
        mcpServers: {
          'vibe-seller': {
            url,
            headers: { Authorization: `Bearer ${token}` },
          },
        },
      },
      null,
      2,
    ),
    qoder: JSON.stringify(
      {
        mcpServers: {
          'vibe-seller': {
            type: 'http',
            url,
            headers: { Authorization: `Bearer ${token}` },
          },
        },
      },
      null,
      2,
    ),
    hermes: [
      'mcp_servers:',
      '  vibe-seller:',
      `    url: "${url}"`,
      '    headers:',
      `      Authorization: "Bearer ${token}"`,
    ].join('\n'),
    pi: `pi mcp add vibe-seller --url ${url} --header "Authorization=Bearer ${token}"`,
  }
}

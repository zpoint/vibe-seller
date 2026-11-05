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
 * The shapes match the verified Claude Code / Codex / WorkBuddy MCP
 * config formats:
 *   - Claude Code: `claude mcp add --transport http <name> <url> --header …`
 *   - Codex:       `~/.codex/config.toml` → `[mcp_servers.<name>]`
 *                  with `bearer_token_env_var` pointing at an env var
 *   - WorkBuddy:   `~/.workbuddy/mcp.json` → `mcpServers.<name>.headers`
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
  /** A shell line exporting the token for Codex. */
  codexExport: string
  /** The `~/.codex/config.toml` block. */
  codexToml: string
  /** The `~/.workbuddy/mcp.json` document. */
  workbuddy: string
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
    claudeCode: `claude mcp add --transport http vibe-seller ${url} --header "Authorization: Bearer ${token}"`,
    codexExport: `export VIBE_SELLER_TOKEN=${token}`,
    codexToml: `[mcp_servers.vibe-seller]\nurl = "${url}"\nbearer_token_env_var = "VIBE_SELLER_TOKEN"`,
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
  }
}

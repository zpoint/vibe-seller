/**
 * Unit tests for the token-page snippet builder: every client config
 * is built against the EDITABLE server address (not a captured
 * window.location.origin), and the loopback warning fires exactly for
 * the addresses only the server's own machine can reach.
 */
import { describe, it, expect } from 'vitest'
import {
  buildSnippets,
  hostnameOf,
  isLoopbackAddress,
} from '../lib/apiTokenSnippets'

const TOKEN = 'vst_testtoken123'

describe('buildSnippets', () => {
  it('points every URL-bearing snippet at the given address', () => {
    const s = buildSnippets(TOKEN, 'http://192.168.1.50:7777')
    const url = 'http://192.168.1.50:7777/api/mcp'
    expect(s.claudeCode).toContain(
      `claude mcp add --scope user --transport http vibe-seller ${url}`,
    )
    expect(s.claudeCode).toContain(`Authorization: Bearer ${TOKEN}`)
    expect(s.codexForm).toEqual({
      name: 'vibe-seller',
      url,
      headerName: 'Authorization',
      headerValue: `Bearer ${TOKEN}`,
    })
    expect(s.codex).toContain(`url = "${url}"`)
    expect(s.codex).toContain(
      `http_headers = { "Authorization" = "Bearer ${TOKEN}" }`,
    )
    const workbuddy = JSON.parse(s.workbuddy)
    expect(workbuddy.mcpServers['vibe-seller'].url).toBe(url)
    expect(workbuddy.mcpServers['vibe-seller'].headers.Authorization).toBe(
      `Bearer ${TOKEN}`,
    )
    const qoder = JSON.parse(s.qoder)
    expect(qoder.mcpServers['vibe-seller']).toEqual({
      type: 'http',
      url,
      headers: { Authorization: `Bearer ${TOKEN}` },
    })
    // The exact block `hermes mcp test` connected with: Hermes takes the
    // entry under mcp_servers in ~/.hermes/config.yaml.
    expect(s.hermes).toBe(
      [
        'mcp_servers:',
        '  vibe-seller:',
        `    url: "${url}"`,
        '    headers:',
        `      Authorization: "Bearer ${TOKEN}"`,
      ].join('\n'),
    )
    // Hermes Desktop's 「Add your own」 box reads the connector
    // document, not YAML — the same one WorkBuddy's editor takes.
    expect(s.hermesJson).toBe(s.workbuddy)
    // Pi's --header takes KEY=VALUE, not the "Key: value" curl form.
    expect(s.pi).toBe(
      `pi mcp add vibe-seller --url ${url} --header "Authorization=Bearer ${TOKEN}"`,
    )
  })

  it('carries the token verbatim, with no shell-export step', () => {
    const s = buildSnippets(TOKEN, 'http://192.168.1.50:7777')
    expect(s.raw).toBe(TOKEN)
    // The export + bearer_token_env_var shape is gone: the Codex
    // desktop app does not inherit shell exports, so the token
    // travels in http_headers instead.
    expect(s.codex).toContain(TOKEN)
    expect(s.codex).not.toContain('export')
    expect(s.codex).not.toContain('bearer_token_env_var')
  })

  it('accepts an address with a trailing slash without doubling the path', () => {
    const s = buildSnippets(TOKEN, 'http://192.168.1.50:7777/')
    expect(s.claudeCode).toContain('http://192.168.1.50:7777/api/mcp')
  })

  it('uses a changed address, not the browsing origin', () => {
    // The field is editable: the agent may reach the server by a
    // different address than the browser did.
    const edited = buildSnippets(TOKEN, 'https://vibe.example.com')
    expect(edited.claudeCode).toContain('https://vibe.example.com/api/mcp')
    expect(edited.claudeCode).not.toContain('localhost')
    expect(edited.claudeCode).not.toContain('127.0.0.1')
  })
})

describe('buildSnippets().codexForm', () => {
  // Codex desktop (Windows + macOS) adds an MCP server through a form
  // — Settings › Plugins › Add › Add MCP server, type Streamable HTTP —
  // which takes one value per field. Those four values are the product
  // surface here, so they are checked as values, not as formatted text.
  it('gives the four form fields for a LAN address', () => {
    expect(buildSnippets(TOKEN, 'http://192.168.1.50:7777').codexForm).toEqual({
      name: 'vibe-seller',
      url: 'http://192.168.1.50:7777/api/mcp',
      headerName: 'Authorization',
      headerValue: `Bearer ${TOKEN}`,
    })
  })

  it('follows the edited address, trailing slash included', () => {
    // The same root the paste blocks use — no doubled `/api//mcp`, no
    // stale browsing origin.
    expect(buildSnippets(TOKEN, 'https://vibe.example.com/').codexForm.url).toBe(
      'https://vibe.example.com/api/mcp',
    )
    expect(buildSnippets(TOKEN, '  http://10.0.0.5:7777  ').codexForm.url).toBe(
      'http://10.0.0.5:7777/api/mcp',
    )
  })

  it('carries the token verbatim and keeps the TOML block intact', () => {
    const s = buildSnippets(TOKEN, 'http://192.168.1.50:7777')
    expect(s.codexForm.headerValue).toBe(`Bearer ${TOKEN}`)
    // The form values are an addition: the file-edit route still ships
    // the same config.toml block as before.
    expect(s.codex).toBe(
      '[mcp_servers.vibe-seller]\n' +
        'url = "http://192.168.1.50:7777/api/mcp"\n' +
        `http_headers = { "Authorization" = "Bearer ${TOKEN}" }`,
    )
  })
})

describe('buildSnippets().hermesJson', () => {
  // Hermes Desktop adds this server in its connector dialog
  // (Capabilities › Connectors › Add your own). The box at the top is
  // "Paste a command or a snippet": a JSON `mcpServers` document
  // fills the form in — name, type, URL, header — and is saved as
  // typed. YAML is NOT recognised there (every field lands empty,
  // type stuck on STDIO), so the GUI tab hands over JSON and the
  // config-file tab keeps the YAML.
  it('is the WorkBuddy-shaped document, byte for byte', () => {
    const s = buildSnippets(TOKEN, 'http://192.168.1.50:7777')
    expect(s.hermesJson).toBe(
      [
        '{',
        '  "mcpServers": {',
        '    "vibe-seller": {',
        '      "url": "http://192.168.1.50:7777/api/mcp",',
        '      "headers": {',
        `        "Authorization": "Bearer ${TOKEN}"`,
        '      }',
        '    }',
        '  }',
        '}',
      ].join('\n'),
    )
    // The same document WorkBuddy's editor is handed: one snippet the
    // user can paste into either tool's box.
    expect(s.hermesJson).toBe(s.workbuddy)
  })

  it('carries the full bearer header, pasted as-is', () => {
    const s = buildSnippets(TOKEN, 'http://192.168.1.50:7777')
    const doc = JSON.parse(s.hermesJson)
    expect(Object.keys(doc.mcpServers)).toEqual(['vibe-seller'])
    expect(doc.mcpServers['vibe-seller'].url).toBe(
      'http://192.168.1.50:7777/api/mcp',
    )
    // Not a bare key for the user to prefix: what the copy button
    // copies is what Hermes saves.
    expect(
      doc.mcpServers['vibe-seller'].headers.Authorization,
    ).toMatch(/^Bearer /)
    expect(doc.mcpServers['vibe-seller'].headers.Authorization).toBe(
      `Bearer ${TOKEN}`,
    )
  })

  it('follows the edited server address', () => {
    expect(
      JSON.parse(buildSnippets(TOKEN, 'https://vibe.example.com/').hermesJson)
        .mcpServers['vibe-seller'].url,
    ).toBe('https://vibe.example.com/api/mcp')
  })

  it('leaves the config.yaml block as it was', () => {
    // Hermes keeps both routes: adding the JSON document must not
    // touch the YAML the config-file tab hands out.
    const s = buildSnippets(TOKEN, 'http://192.168.1.50:7777')
    expect(s.hermes).toContain('mcp_servers:')
    expect(s.hermes).not.toContain('mcpServers')
    expect(s.hermes).not.toContain('{')
  })
})

describe('isLoopbackAddress', () => {
  it('warns for every loopback spelling', () => {
    expect(isLoopbackAddress('http://localhost:7777')).toBe(true)
    expect(isLoopbackAddress('http://127.0.0.1:7777')).toBe(true)
    expect(isLoopbackAddress('http://[::1]:7777')).toBe(true)
  })

  it('stays quiet for addresses other machines can reach', () => {
    expect(isLoopbackAddress('http://192.168.1.50:7777')).toBe(false)
    expect(isLoopbackAddress('https://vibe.example.com')).toBe(false)
    expect(isLoopbackAddress('http://10.0.0.5:7777')).toBe(false)
  })

  it('has no opinion for an unparseable address', () => {
    // The snippets then show the raw text — its own feedback.
    expect(isLoopbackAddress('not a url')).toBe(false)
    expect(isLoopbackAddress('')).toBe(false)
  })
})

describe('hostnameOf', () => {
  it('extracts the host without brackets or port', () => {
    expect(hostnameOf('http://127.0.0.1:7777')).toBe('127.0.0.1')
    expect(hostnameOf('http://[::1]:7777')).toBe('::1')
    expect(hostnameOf('https://vibe.example.com')).toBe('vibe.example.com')
  })
})

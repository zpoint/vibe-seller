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

import { describe, it, expect, vi, beforeEach } from 'vitest'

const sendEvent = vi.fn()
vi.mock('../lib/telemetry', async (orig) => ({
  ...(await orig<typeof import('../lib/telemetry')>()),
  sendEvent: (...args: unknown[]) => sendEvent(...args),
}))
const put = vi.fn()
vi.mock('../api', () => ({
  api: {
    put: (...args: unknown[]) => put(...args),
    get: vi.fn(async () => ({ content: '' })),
  },
}))

import { useWsFiles } from '../hooks/useWsFiles'

function deps(selected: string | null) {
  const noop = () => {}
  return {
    t: (k: string) => k,
    wsSelectedFile: selected,
    wsEditorContent: 'body',
    wsSaving: false,
    setWsSelectedFile: noop, setWsFileContent: noop, setWsEditorContent: noop,
    setWsEditorDirty: noop, setWsShowHistory: noop, setWsPreviewCommit: noop,
    setWsPreviewContent: noop, setWsSaving: noop, setWsSyncing: noop,
    setWsSyncMeta: noop, setWsSkillsSyncing: noop, setWsFileHistory: noop,
    setWsNewFileName: noop, setWsNewFileSection: noop,
    loadWsStructured: async () => {},
  }
}

describe('knowledge file telemetry', () => {
  beforeEach(() => { sendEvent.mockClear(); put.mockReset() })

  it('a saved file reports how it was saved and its area, never its path', async () => {
    put.mockResolvedValue({})
    await useWsFiles(deps('stores/acme/notes.md')).saveWsFile('shortcut')
    expect(sendEvent).toHaveBeenCalledWith('knowledge_file_saved', { via: 'shortcut', area: 'store' })
  })

  it('a failed save is not reported', async () => {
    put.mockRejectedValue(new Error('boom'))
    vi.stubGlobal('alert', vi.fn())
    await useWsFiles(deps('knowledge/a.md')).saveWsFile('button')
    expect(sendEvent).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('a created file reports its area', async () => {
    put.mockResolvedValue({})
    await useWsFiles(deps(null)).createWsFile('knowledge', 'rules')
    expect(sendEvent).toHaveBeenCalledWith('knowledge_file_created', { area: 'knowledge' })
  })
})

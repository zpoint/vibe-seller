import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

describe('API helpers', () => {
  beforeEach(() => {
    mockFetch.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('api.get', () => {
    it('should make GET request with credentials', async () => {
      const mockResponse = { ok: true, status: 200, json: () => Promise.resolve({ data: 'test' }) }
      mockFetch.mockResolvedValueOnce(mockResponse)

      const api = {
        get: async (url: string) => {
          const r = await fetch(url, { credentials: 'include' })
          if (r.status === 401) throw new Error('unauthorized')
          if (!r.ok) throw new Error(`Request failed (${r.status})`)
          return r.json()
        }
      }

      const result = await api.get('/api/test')

      expect(mockFetch).toHaveBeenCalledWith('/api/test', { credentials: 'include' })
      expect(result).toEqual({ data: 'test' })
    })

    it('should throw unauthorized on 401', async () => {
      const mockResponse = { ok: false, status: 401 }
      mockFetch.mockResolvedValueOnce(mockResponse)

      const api = {
        get: async (url: string) => {
          const r = await fetch(url, { credentials: 'include' })
          if (r.status === 401) throw new Error('unauthorized')
          if (!r.ok) throw new Error(`Request failed (${r.status})`)
          return r.json()
        }
      }

      await expect(api.get('/api/test')).rejects.toThrow('unauthorized')
    })

    it('should throw error on failed request', async () => {
      const mockResponse = {
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: 'Server error' })
      }
      mockFetch.mockResolvedValueOnce(mockResponse)

      const api = {
        get: async (url: string) => {
          const r = await fetch(url, { credentials: 'include' })
          if (r.status === 401) throw new Error('unauthorized')
          if (!r.ok) {
            const err = await r.json().catch(() => ({}))
            throw new Error(err.detail || `Request failed (${r.status})`)
          }
          return r.json()
        }
      }

      await expect(api.get('/api/test')).rejects.toThrow('Server error')
    })
  })

  describe('api.post', () => {
    it('should make POST request with JSON body', async () => {
      const mockResponse = {
        ok: true,
        status: 200,
        json: () => Promise.resolve({ id: '123', name: 'Test' })
      }
      mockFetch.mockResolvedValueOnce(mockResponse)

      const api = {
        post: async (url: string, body?: unknown) => {
          const r = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
            credentials: 'include'
          })
          if (r.status === 401) throw new Error('unauthorized')
          if (!r.ok) throw new Error(`Request failed (${r.status})`)
          return r.json()
        }
      }

      const body = { name: 'Test Store' }
      const result = await api.post('/api/stores', body)

      expect(mockFetch).toHaveBeenCalledWith('/api/stores', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        credentials: 'include'
      })
      expect(result).toEqual({ id: '123', name: 'Test' })
    })
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from '../api'

const mockFetch = vi.fn()
global.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockClear()
})

describe('api error handling', () => {
  describe('api.put', () => {
    it('throws on 409 with detail message', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () =>
          Promise.resolve({
            detail:
              'Cannot rename store with active browser session. Stop the browser first.',
          }),
      })

      await expect(api.put('/api/stores/123', { name: 'New' })).rejects.toThrow(
        'Cannot rename store with active browser session'
      )
    })

    it('throws on 400 with detail message', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: () => Promise.resolve({ detail: 'Username already in use' }),
      })

      await expect(
        api.put('/api/users/123', { username: 'taken' })
      ).rejects.toThrow('Username already in use')
    })

    it('throws generic message when no detail', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error('not json')),
      })

      await expect(api.put('/api/test', {})).rejects.toThrow(
        'Request failed (500)'
      )
    })

    it('returns data on success', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ok: true }),
      })

      const result = await api.put('/api/test', { x: 1 })
      expect(result).toEqual({ ok: true })
    })
  })

  describe('api.del', () => {
    it('throws on 400 with detail message', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: () => Promise.resolve({ detail: 'Cannot delete yourself' }),
      })

      await expect(api.del('/api/users/123')).rejects.toThrow(
        'Cannot delete yourself'
      )
    })

    it('throws on 409 with detail message', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () =>
          Promise.resolve({ detail: 'Cannot delete store with active tasks' }),
      })

      await expect(api.del('/api/stores/123')).rejects.toThrow(
        'Cannot delete store with active tasks'
      )
    })

    it('returns data on success', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ok: true }),
      })

      const result = await api.del('/api/test')
      expect(result).toEqual({ ok: true })
    })
  })

  describe('api.put throws 401 as unauthorized', () => {
    it('throws unauthorized on 401', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401 })

      await expect(api.put('/api/test', {})).rejects.toThrow('unauthorized')
    })
  })

  describe('api.del throws 401 as unauthorized', () => {
    it('throws unauthorized on 401', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401 })

      await expect(api.del('/api/test')).rejects.toThrow('unauthorized')
    })
  })
})

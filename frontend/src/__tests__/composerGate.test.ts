import { describe, it, expect } from 'vitest'
import type { ConversationItem } from '../types'
import {
  hasPendingImageConfirm,
  isAwaitingUser,
} from '../handlers/composerGate'

const imgItem = (over: Record<string, unknown>): ConversationItem =>
  ({
    id: 'i1',
    type: 'image_request',
    imageRequest: {
      requestId: 'r1',
      prompt: 'p',
      model: 'm',
      models: [],
      referenceImages: [],
      ...over,
    },
  }) as unknown as ConversationItem

describe('composerGate', () => {
  it('a shown, un-acted image confirm card counts as pending', () => {
    expect(hasPendingImageConfirm([imgItem({})])).toBe(true)
  })

  it('a generating / resolved / expired card is NOT pending', () => {
    expect(hasPendingImageConfirm([imgItem({ generating: true })])).toBe(false)
    expect(hasPendingImageConfirm([imgItem({ resolved: true })])).toBe(false)
    expect(hasPendingImageConfirm([imgItem({ expired: true })])).toBe(false)
  })

  it('awaitingUser is true for a pending question OR a live confirm card', () => {
    expect(isAwaitingUser([], true)).toBe(true) // pending question
    expect(isAwaitingUser([imgItem({})], false)).toBe(true) // confirm card
    expect(isAwaitingUser([imgItem({ generating: true })], false)).toBe(false)
    expect(isAwaitingUser([], false)).toBe(false)
  })
})

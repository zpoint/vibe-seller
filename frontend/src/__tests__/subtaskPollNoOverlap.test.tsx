/**
 * The subtask poll never stacks requests on a slow server.
 *
 * Observed (review): SubtaskList polls every 10 s and each request may
 * take up to the 30 s deadline, so against a slow server up to three
 * polls were in flight at once and kept piling on.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, render } from '@testing-library/react'

const get = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({ api: { get } }))

import { SubtaskList } from '../components/SubtaskList'

describe('SubtaskList polling', () => {
  afterEach(() => { vi.useRealTimers(); get.mockReset() })

  it('does not start a poll while the previous one is in flight', async () => {
    vi.useFakeTimers()
    let answer: (v: unknown) => void = () => {}
    get.mockImplementation(() => new Promise(resolve => { answer = resolve }))
    render(<SubtaskList parentTaskId="task-1" onSelect={() => {}} stores={[]} />)
    expect(get).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(25_000) })
    expect(get).toHaveBeenCalledTimes(1)

    await act(async () => { answer([]) })
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('re-reads the children the moment the parent changes status', async () => {
    // A parent completes when its last child finishes; waiting for the
    // next 10 s poll showed that child "Running" under a completed parent.
    vi.useFakeTimers()
    get.mockResolvedValue([])
    const { rerender } = render(
      <SubtaskList parentTaskId="task-1" parentStatus="waiting" onSelect={() => {}} stores={[]} />,
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(get).toHaveBeenCalledTimes(1)

    rerender(<SubtaskList parentTaskId="task-1" parentStatus="completed" onSelect={() => {}} stores={[]} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(get).toHaveBeenCalledTimes(2)
  })
})

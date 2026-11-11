/**
 * Apply `fn` to the row with `id`, keeping the list's identity when that
 * changes nothing (no such row, or `fn` returned the row unchanged).
 *
 * SSE events arrive many times a second — one per streamed token, for
 * every running task on the server. A plain `prev.map(...)` returns a
 * new array for each, so React re-rendered the whole app for events
 * about tasks that are not even on screen. Returning `list` itself lets
 * React bail out.
 */
export function patchById<T extends { id: string }>(
  list: T[],
  id: string,
  fn: (row: T) => T,
): T[] {
  let changed = false
  const next = list.map(row => {
    if (row.id !== id) return row
    const updated = fn(row)
    if (updated !== row) changed = true
    return updated
  })
  return changed ? next : list
}

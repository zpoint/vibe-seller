import type { Store } from '../types'

/** The ASCII folder-name residue used by the server's create/rename guard. */
export function storeNameSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, '-')
    .replace(/_/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
}

const isAscii = (value: string) =>
  Array.from(value).every(char => char.charCodeAt(0) <= 127)
const sameName = (a: string, b: string) =>
  a.trim().toLowerCase() === b.trim().toLowerCase()

/**
 * The client counterpart of routers/stores.py::_refuse_taken_slug. The
 * server remains authoritative when the list is stale or another client
 * creates the same name first.
 */
export function findStoreNameCollision(
  name: string,
  stores: Store[],
): { store: Store; slug: string } | null {
  const slug = storeNameSlug(name)
  // The server gives an all-non-ASCII name an id-based folder instead.
  if (!slug) return null
  const store = stores.find(other => {
    if (storeNameSlug(other.name) !== slug) return false
    return (isAscii(name) && isAscii(other.name)) || sameName(name, other.name)
  })
  return store ? { store, slug } : null
}

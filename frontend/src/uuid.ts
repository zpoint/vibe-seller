/**
 * Generate a v4 UUID using crypto.getRandomValues(), which is
 * available in ALL browser contexts (including non-secure / plain HTTP).
 *
 * crypto.randomUUID() requires a Secure Context (HTTPS or localhost),
 * so it breaks when accessing via LAN IP or Tailscale hostnames over HTTP.
 */
export function uuid(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  // Set version 4 (0100) in byte 6
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  // Set variant 10xx in byte 8
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
  return (
    hex.slice(0, 8) + '-' +
    hex.slice(8, 12) + '-' +
    hex.slice(12, 16) + '-' +
    hex.slice(16, 20) + '-' +
    hex.slice(20)
  )
}

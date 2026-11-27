import type { BrowserVendor } from '../types'

/**
 * The create-store form's backend options, from the vendor registry:
 * selectable vendors for this server's platform, in registration order.
 *
 * While the platform is still unknown, only all-platform vendors are
 * offered (the pre-registry behavior: never a platform-gated one).
 */
export const ALL_PLATFORM_COUNT = 4 // windows / wsl / mac / linux

export function vendorBackendOptions(
  vendors: BrowserVendor[] | null,
  serverPlatform: string | null,
): BrowserVendor[] {
  return (vendors || []).filter(
    v =>
      v.selectable &&
      (v.platforms.length === ALL_PLATFORM_COUNT ||
        (serverPlatform != null && v.platforms.includes(serverPlatform))),
  )
}

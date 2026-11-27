/** Shapes of GET /api/integrations/cli (one card per platform) and of a
 *  running sign-in (GET /api/integrations/cli/{key}/login). */

export interface LoginView {
  state: 'starting' | 'waiting' | 'succeeded' | 'failed' | 'cancelled'
  url: string | null
  user_code: string | null
  qr: boolean
  message: string | null
  step: number
  steps: number
  hint: string | null
  /** What the person must know about the outcome — several can apply
   *  (scopes withheld AND a sign-in that cannot renew). Set by the
   *  server's parsers: an i18n key, its detail and a link to act on. */
  notices?: Notice[]
  /** Why the sign-in ended badly when no notice explains it: the
   *  runner's ``timeout`` or ``enable_failed`` (i18n key). */
  error?: string | null
  /** Signed in, and the server is still enabling the platform: the
   *  outcome is not final yet, so the page keeps polling. */
  finishing?: boolean
  /** The approval comes back as a redirect to ``callback`` (a port on
   *  the server's computer): a browser elsewhere cannot reach it, so the
   *  page takes the address it ended on and the server relays it. */
  paste_back?: boolean
  callback?: string | null
}

export interface Notice {
  key: string
  detail: string | null
  url: string | null
}

export interface CliCard {
  key: string
  title: string
  binary: string
  homepage: string
  installed: boolean
  version: string | null
  enabled: boolean
  auth: { usable: boolean; reason: string; identity: string | null } | null
  login: Partial<LoginView> | null
  /** The one-time setup a sign-in needs (its form), and whether the
   *  sign-in waits on it now. */
  setup?: string | null
  needs_setup?: boolean
  /** Said on the row until the platform is enabled (i18n key). */
  notice?: string | null
  /** Its authorization can expire while the CLI still says signed in. */
  checks_expiry?: boolean
}

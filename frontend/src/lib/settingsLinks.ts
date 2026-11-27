import type { SettingsTab } from '../views/SettingsView'

/**
 * Stable targets the agent's prompts tell it to link, each opening a
 * Settings tab in place. The agent may translate the link TEXT, but
 * keeps the href — so the page renders an in-app button regardless of
 * language instead of a dead #anchor:
 *
 *   #vision-setup  no image key yet (app/prompts/vision_setup_breadcrumb.md)
 *   #integrations[-<platform>]  a workplace sign-in or permission
 *                  expired (app/cli_integrations/skills.py umbrella
 *                  rules) — with the platform, its row is scrolled to
 */
export const SETTINGS_LINKS: Record<string, SettingsTab> = {
  '#vision-setup': 'aiAgent',
  '#integrations': 'integrations',
}

/** Where a settings link leads: the tab, and the section to scroll to. */
export interface SettingsTarget {
  tab: SettingsTab
  anchor?: string
}

const PLATFORM_ROW = /^#integrations-([a-z0-9-]+)$/

/** The Settings target of *href*, or undefined for any other link. */
export function settingsTarget(href: string | undefined): SettingsTarget | undefined {
  if (!href) return undefined
  const tab = SETTINGS_LINKS[href]
  if (tab) return { tab }
  const row = PLATFORM_ROW.exec(href)
  return row ? { tab: 'integrations', anchor: `cli-${row[1]}` } : undefined
}

const MARKDOWN_LINK = /\[([^\]]+)\]\((#[\w-]+)\)/g

/** A plain-text message (a task's error) with its settings links taken
 *  out: the text keeps each link's label, and ``links`` lists them for
 *  the page to render as buttons. Other links are left as written. */
export function settingsLinksIn(text: string): { text: string; links: (SettingsTarget & { label: string })[] } {
  const links: (SettingsTarget & { label: string })[] = []
  const plain = text.replace(MARKDOWN_LINK, (whole, label: string, href: string) => {
    const target = settingsTarget(href)
    if (!target) return whole
    links.push({ label, ...target })
    return label
  })
  return { text: plain, links }
}

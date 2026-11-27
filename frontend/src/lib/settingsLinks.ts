import type { SettingsTab } from '../views/SettingsView'

/**
 * Stable targets the agent's prompts tell it to link, each opening a
 * Settings tab in place. The agent may translate the link TEXT, but
 * keeps the href — so the page renders an in-app button regardless of
 * language instead of a dead #anchor:
 *
 *   #vision-setup  no image key yet (app/prompts/vision_setup_breadcrumb.md)
 *   #integrations  a workplace sign-in or permission expired
 *                  (app/cli_integrations/skills.py umbrella rules)
 */
export const SETTINGS_LINKS: Record<string, SettingsTab> = {
  '#vision-setup': 'aiAgent',
  '#integrations': 'integrations',
}

const MARKDOWN_LINK = /\[([^\]]+)\]\((#[\w-]+)\)/g

/** A plain-text message (a task's error) with its settings links taken
 *  out: the text keeps each link's label, and ``links`` lists them for
 *  the page to render as buttons. Other links are left as written. */
export function settingsLinksIn(text: string): { text: string; links: { label: string; tab: SettingsTab }[] } {
  const links: { label: string; tab: SettingsTab }[] = []
  const plain = text.replace(MARKDOWN_LINK, (whole, label: string, href: string) => {
    const tab = SETTINGS_LINKS[href]
    if (!tab) return whole
    links.push({ label, tab })
    return label
  })
  return { text: plain, links }
}

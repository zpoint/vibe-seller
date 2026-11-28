import type { ComponentProps } from 'react'
import { settingsTarget } from '../../lib/settingsLinks'
import type { SettingsTab } from '../../views/SettingsView'

type Props = ComponentProps<'a'> & {
  /** Open a Settings tab (and the section *anchor* names). Without it a
   *  Settings link stays a plain link. */
  onOpenSettings?: (tab: SettingsTab, anchor?: string) => void
  /** react-markdown's syntax-tree node: never reaches the DOM. */
  node?: unknown
}

/**
 * A link in an agent's markdown (its messages, a task's result): a
 * Settings link the agent's prompts give it (lib/settingsLinks.ts —
 * an image key to add, a workplace sign-in to renew) is an in-app
 * button, whatever language the agent wrote its text in; any other link
 * is a link.
 */
export function MarkdownLink({ href, children, onOpenSettings, node, ...props }: Props) {
  void node
  const target = settingsTarget(href)
  if (target && onOpenSettings) {
    return (
      <button
        type="button"
        data-testid={`settings-link-${target.tab}`}
        onClick={() => onOpenSettings(target.tab, target.anchor)}
        className="not-prose inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 bg-indigo-50 px-2.5 py-1 text-[14px] font-medium text-indigo-700 hover:bg-indigo-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
      >
        {children}
        <span aria-hidden="true">→</span>
      </button>
    )
  }
  return <a href={href} {...props}>{children}</a>
}

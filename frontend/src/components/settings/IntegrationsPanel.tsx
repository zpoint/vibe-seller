import { ChatBotSection } from '../ChatBotSection'
import { ApiTokensPanel } from './ApiTokensPanel'
import { Dida365Panel } from './Dida365Panel'
import { AmazonAdsPanel } from './AmazonAdsPanel'
import { WorkplaceCliPanel } from './WorkplaceCliPanel'

export function IntegrationsPanel({
  isAdmin = false,
}: {
  isAdmin?: boolean
}) {
  return (
    <div className="space-y-4">
      {/* Order: ads service, API keys, chat bots, workplace CLIs,
          Google Workspace, TickTick. */}
      <AmazonAdsPanel />
      <ApiTokensPanel />
      <ChatBotSection isAdmin={isAdmin} />
      <WorkplaceCliPanel isAdmin={isAdmin} />
      <WorkplaceCliPanel isAdmin={isAdmin} google />
      <Dida365Panel />
    </div>
  )
}

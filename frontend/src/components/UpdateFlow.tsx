import type { useUpdateFlow } from '../hooks/useUpdateFlow'
import { UpdateAppliedModal } from './UpdateAppliedModal'
import { UpdateAvailableModal } from './UpdateAvailableModal'

type UpdateFlowState = ReturnType<typeof useUpdateFlow>

/**
 * The update modals, driven entirely by the `useUpdateFlow` state the
 * App holds (the same object feeds the sidebar chip). Lives here so
 * App.tsx stays the layout shell it is — one element, not a ten-prop
 * call site — and the modal's visibility rule ("needs the check
 * result; needs the flow to say open") has one owner.
 *
 * One surface at a time: the post-update notes are what the admin came
 * here to read after an upgrade, so they yield to the update dialog
 * while it is up (`flow.updatedOpen` already says "nothing else is
 * open") and return when it closes.
 */
export function UpdateFlow({
  flow,
  isAdmin,
}: {
  flow: UpdateFlowState
  isAdmin: boolean
}) {
  if (flow.updatedOpen && flow.updatedNotes) {
    return (
      <UpdateAppliedModal
        range={flow.updatedNotes}
        releasesPageUrl={flow.updateCheck?.releases_page_url}
        onClose={flow.closeUpdated}
      />
    )
  }
  if (!flow.updateCheck || !flow.open) return null
  return (
    <UpdateAvailableModal
      check={flow.updateCheck}
      status={flow.status}
      isAdmin={isAdmin}
      onClose={flow.closeModal}
      onRestart={flow.restart}
      restartError={flow.restartError}
      restartTimedOut={flow.restartTimedOut}
    />
  )
}

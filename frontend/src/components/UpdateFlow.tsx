import type { useUpdateFlow } from '../hooks/useUpdateFlow'
import { UpdateAvailableModal } from './UpdateAvailableModal'

type UpdateFlowState = ReturnType<typeof useUpdateFlow>

/**
 * The update modal, driven entirely by the `useUpdateFlow` state the
 * App holds (the same object feeds the sidebar chip). Lives here so
 * App.tsx stays the layout shell it is — one element, not a ten-prop
 * call site — and the modal's visibility rule ("needs the check
 * result; needs the flow to say open") has one owner.
 */
export function UpdateFlow({
  flow,
  isAdmin,
}: {
  flow: UpdateFlowState
  isAdmin: boolean
}) {
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

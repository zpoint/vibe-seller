// The app shell's half of "set up AI first": every "new task" / "new
// schedule" button asks the server whether AI is set up before opening
// its form. Not set up → AiSetupPrompt instead of the form, so nobody
// types out a task that could not run.
//
// Checked on EVERY click, not only the first: the thing being prevented
// is "no usable AI", which is just as true for a user whose first task
// already failed or who deleted their only model. The server answers
// "set up" when it cannot tell, so this never becomes a new way to be
// blocked. Creating the task itself is not checked.
import { useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { AiSetupPrompt } from '../components/AiSetupPrompt'
import { aiSetupStore, fetchAiReadiness } from '../lib/aiSetup'
import { settingsTabToSlug } from '../lib/route'

export function useAiSetupGate() {
  const navigate = useNavigate()
  const [promptOpen, setPromptOpen] = useState(false)
  const checking = useRef(false)

  /** Open a create form, or the dialog when no AI is set up. */
  const guardOpen = async (open: () => void) => {
    if (checking.current) return  // double click while asking
    checking.current = true
    try {
      const r = await fetchAiReadiness()
      if (r && !r.ready) setPromptOpen(true)
      else open()
    } finally {
      checking.current = false
    }
  }

  const prompt = promptOpen ? (
    <AiSetupPrompt
      onChoose={choice => {
        if (choice === 'docs') return  // new tab; this dialog stays
        setPromptOpen(false)
        if (choice === 'settings') {
          aiSetupStore.requestGuide()
          navigate({ to: '/settings/$tab', params: { tab: settingsTabToSlug('aiAgent') } })
        }
      }}
    />
  ) : null

  return { guardOpen, prompt }
}

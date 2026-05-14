import { useEffect, useRef } from 'react'
import { api } from '../api'
import type { Task, AgentMessage, TodoItem, ConversationItem } from '../types'
import { uuid } from '../uuid'

/** Finalize any streaming thinking block — set isStreaming to false. */
function _finalizeThinking(items: ConversationItem[]): ConversationItem[] {
  const last = items[items.length - 1]
  if (last?.type === 'thinking' && last.thinking?.isStreaming) {
    return [...items.slice(0, -1), {
      ...last,
      thinking: { ...last.thinking, isStreaming: false },
    }]
  }
  return items
}

interface UseSSEParams {
  selectedTaskId: string | undefined
  appView: string
  /** Currently-selected store id, or null for "All stores" / no store view. */
  selectedStoreId?: string | null
  /** True when the "All stores" (store_id IS NULL) list is on screen. */
  showAllTasks?: boolean
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setSteps: React.Dispatch<React.SetStateAction<import('../types').TaskStep[]>>
  setScreenshots: React.Dispatch<React.SetStateAction<Record<string, string>>>
  setAgentMessages: React.Dispatch<React.SetStateAction<AgentMessage[]>>
  setTodoItems: React.Dispatch<React.SetStateAction<TodoItem[]>>
  setConversationItems: React.Dispatch<React.SetStateAction<ConversationItem[]>>
  setPendingQuestions: React.Dispatch<React.SetStateAction<{ request_id: string; questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] } | null>>
  setSelectedAnswers: React.Dispatch<React.SetStateAction<Record<string, string>>>
  setOtherInputs: React.Dispatch<React.SetStateAction<Record<string, string>>>
  setShowOtherInput: React.Dispatch<React.SetStateAction<Record<string, boolean>>>
  setLogs: React.Dispatch<React.SetStateAction<string[]>>
  questionBannerRef: React.RefObject<HTMLDivElement | null>
  setScheduleTasks?: React.Dispatch<React.SetStateAction<Task[]>>
  loadScheduleTasks?: () => void
  loadSchedules?: () => void
  loadTasks?: () => void
  setWsAssistantMessages?: React.Dispatch<React.SetStateAction<AgentMessage[]>>
  setWsAssistantRunning?: React.Dispatch<React.SetStateAction<boolean>>
  loadWsStructured?: () => void
}

export function useSSE({
  selectedTaskId,
  appView,
  selectedStoreId = null,
  showAllTasks = false,
  setTasks,
  setSelectedTask,
  setSteps,
  setScreenshots,
  setAgentMessages,
  setTodoItems,
  setConversationItems,
  setPendingQuestions,
  setSelectedAnswers,
  setOtherInputs,
  setShowOtherInput,
  setLogs,
  questionBannerRef,
  setScheduleTasks,
  loadScheduleTasks,
  loadSchedules,
  loadTasks,
  setWsAssistantMessages,
  setWsAssistantRunning,
  loadWsStructured,
}: UseSSEParams) {
  // Use refs so the EventSource connection stays stable across
  // task/schedule/store selections (no reconnect gap that drops
  // SSE events).  Callbacks that capture selectedSchedule,
  // selectedStore etc. would go stale without this.
  const selectedTaskIdRef = useRef(selectedTaskId)
  const selectedStoreIdRef = useRef(selectedStoreId)
  const showAllTasksRef = useRef(showAllTasks)
  const loadScheduleTasksRef = useRef(loadScheduleTasks)
  const loadSchedulesRef = useRef(loadSchedules)
  const loadTasksRef = useRef(loadTasks)
  useEffect(() => { selectedTaskIdRef.current = selectedTaskId }, [selectedTaskId])
  useEffect(() => { selectedStoreIdRef.current = selectedStoreId }, [selectedStoreId])
  useEffect(() => { showAllTasksRef.current = showAllTasks }, [showAllTasks])
  useEffect(() => { loadScheduleTasksRef.current = loadScheduleTasks }, [loadScheduleTasks])
  useEffect(() => { loadSchedulesRef.current = loadSchedules }, [loadSchedules])
  useEffect(() => { loadTasksRef.current = loadTasks }, [loadTasks])

  useEffect(() => {
    const es = new EventSource('/api/sse')
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'ping') return
        if (data.type === 'task_created' && data.task) {
          // Live update for tasks created in another tab/by another
          // user.  Only insert into the list the user is currently
          // viewing — otherwise a same-store task created elsewhere
          // would pollute an unrelated store's list.  Dedupe by id
          // because the tab that posted this task already prepended
          // it from the POST response.
          const t = data.task as Task
          const matchesView =
            (t.store_id && t.store_id === selectedStoreIdRef.current) ||
            (t.store_id == null && showAllTasksRef.current)
          if (matchesView) {
            setTasks(prev =>
              prev.some(x => x.id === t.id) ? prev : [t, ...prev],
            )
          }
          return
        }
        if (data.type === 'task_update') {
          const patch: Partial<Task> = { status: data.status, error: data.error }
          if (data.plan !== undefined) patch.plan = data.plan
          setTasks(prev => prev.map(t => t.id === data.task_id ? { ...t, ...patch } : t))
          if (setScheduleTasks) {
            setScheduleTasks(prev => prev.map(t => t.id === data.task_id ? { ...t, ...patch } : t))
          }

          setSelectedTask(prev => {
            if (!prev || prev.id !== data.task_id) return prev

            // Plan update → append plan card to conversation
            if (data.plan !== undefined && selectedTaskIdRef.current === data.task_id) {
              setConversationItems(items => {
                const maxVersion = items.reduce((max, i) => i.type === 'plan' && i.plan ? Math.max(max, i.plan.version) : max, 0)
                const newVersion = maxVersion + 1
                const updated = items.map(item =>
                  item.type === 'plan' ? { ...item, plan: { ...item.plan!, isCurrent: false } } : item
                )
                return [...updated, {
                  id: `plan-${newVersion}-${Date.now()}`,
                  type: 'plan' as const,
                  timestamp: new Date().toISOString(),
                  plan: { version: newVersion, content: data.plan, isCurrent: true },
                }]
              })
            }

            // Status transition to running → append execution separator
            if (data.status === 'running' && prev.status !== 'running' && selectedTaskIdRef.current === data.task_id) {
              setConversationItems(items => {
                if (items.some(i => i.type === 'execution_separator')) return items
                return [...items, {
                  id: `exec-sep-${Date.now()}`,
                  type: 'execution_separator' as const,
                  timestamp: new Date().toISOString(),
                }]
              })
            }

            return { ...prev, ...patch }
          })
        }
        if (data.type === 'task_steps_created') {
          setSelectedTask(prev => {
            if (prev && prev.id === data.task_id) setSteps(data.steps.map((s: Record<string, unknown>) => ({ ...s, screenshot_id: null, error: null, started_at: null, completed_at: null, action_type: 'navigate' })))
            return prev
          })
        }
        if (data.type === 'step_update') {
          setSteps(prev => prev.map(s => s.id === data.step_id ? { ...s, status: data.status, screenshot_id: data.screenshot_id || s.screenshot_id, error: data.error || s.error } : s))
          if (data.screenshot_b64) {
            setScreenshots(prev => ({ ...prev, [data.step_id]: data.screenshot_b64 }))
          }
        }
        if (data.type === 'task_log') {
          setLogs(prev => [...prev, `[${data.log_type}] ${data.content}`])
        }
        if (data.type === 'task_message') {
          setSelectedTask(prev => {
            if (prev && prev.id === data.task_id) {
              // Update agentMessages (for debug mode)
              if (data.role === 'delta') {
                setAgentMessages(p => {
                  const last = p[p.length - 1]
                  if (last && last.role === '_streaming') {
                    return [...p.slice(0, -1), { role: '_streaming', content: last.content + data.content }]
                  }
                  return [...p, { role: '_streaming', content: data.content }]
                })
                // Update conversation stream
                if (selectedTaskIdRef.current === data.task_id) {
                  setConversationItems(items => {
                    const last = items[items.length - 1]
                    if (last && last.type === 'streaming') {
                      return [...items.slice(0, -1), {
                        ...last,
                        message: { role: '_streaming', content: (last.message?.content || '') + data.content },
                      }]
                    }
                    return [...items, {
                      id: `streaming-${Date.now()}`,
                      type: 'streaming' as const,
                      timestamp: new Date().toISOString(),
                      message: { role: '_streaming', content: data.content },
                    }]
                  })
                }
              } else if (data.role === 'assistant') {
                setAgentMessages(p => {
                  const last = p[p.length - 1]
                  if (last && last.role === '_streaming') {
                    return [...p.slice(0, -1), { role: data.role, content: data.content }]
                  }
                  return [...p, { role: data.role, content: data.content }]
                })
                // Replace streaming with agent_message + finalize thinking
                if (selectedTaskIdRef.current === data.task_id) {
                  setConversationItems(items => {
                    const updated = _finalizeThinking(items)
                    const last = updated[updated.length - 1]
                    if (last && last.type === 'streaming') {
                      return [...updated.slice(0, -1), {
                        id: `msg-${uuid()}`,
                        type: 'agent_message' as const,
                        timestamp: new Date().toISOString(),
                        message: { role: 'assistant', content: data.content },
                      }]
                    }
                    return [...updated, {
                      id: `msg-${uuid()}`,
                      type: 'agent_message' as const,
                      timestamp: new Date().toISOString(),
                      message: { role: 'assistant', content: data.content },
                    }]
                  })
                }
              } else if (data.role === 'user') {
                setAgentMessages(p => [...p, { role: data.role, content: data.content }])
                // Append user message if not already there (optimistic add)
                if (selectedTaskIdRef.current === data.task_id) {
                  setConversationItems(items => {
                    const hasRecent = items.some(i =>
                      i.type === 'user_message' && i.message?.content === data.content &&
                      Date.now() - new Date(i.timestamp).getTime() < 5000
                    )
                    if (hasRecent) return items
                    return [...items, {
                      id: `user-${uuid()}`,
                      type: 'user_message' as const,
                      timestamp: new Date().toISOString(),
                      message: { role: 'user', content: data.content },
                    }]
                  })
                }
              } else if (data.role === 'result') {
                setAgentMessages(p => [...p, { role: data.role, content: data.content }])
                if (selectedTaskIdRef.current === data.task_id) {
                  setConversationItems(items => {
                    const updated = _finalizeThinking(items)
                    const hasResult = updated.some(i => i.type === 'result')
                    if (hasResult) {
                      // Already have a result card — show as regular message
                      return [...updated, {
                        id: `asst-${uuid()}`,
                        type: 'agent_message' as const,
                        timestamp: new Date().toISOString(),
                        message: { role: 'assistant', content: data.content },
                      }]
                    }
                    return [...updated, {
                      id: `result-${uuid()}`,
                      type: 'result' as const,
                      timestamp: new Date().toISOString(),
                      result: data.content,
                    }]
                  })
                }
              } else if (data.role === 'tool_use') {
                setAgentMessages(p => [...p, { role: data.role, content: data.content }])
                if (selectedTaskIdRef.current === data.task_id) {
                  try {
                    const toolInfo = JSON.parse(data.content)
                    setConversationItems(items => {
                      const updated = _finalizeThinking(items)
                      return [...updated, {
                        id: `tool-${uuid()}`,
                        type: 'tool_call' as const,
                        timestamp: new Date().toISOString(),
                        toolCall: toolInfo,
                      }]
                    })
                  } catch {
                    // Malformed JSON — keep in debug only
                  }
                }
              } else if (data.role === 'thinking_delta') {
                if (selectedTaskIdRef.current === data.task_id) {
                  setConversationItems(items => {
                    const last = items[items.length - 1]
                    if (last?.type === 'thinking' && last.thinking?.isStreaming) {
                      return [...items.slice(0, -1), {
                        ...last,
                        thinking: {
                          content: (last.thinking.content || '') + data.content,
                          isStreaming: true,
                        },
                      }]
                    }
                    return [...items, {
                      id: `thinking-${uuid()}`,
                      type: 'thinking' as const,
                      timestamp: new Date().toISOString(),
                      thinking: { content: data.content, isStreaming: true },
                    }]
                  })
                }
              } else if (data.role === 'thinking') {
                setAgentMessages(p => [...p, { role: data.role, content: data.content }])
                if (selectedTaskIdRef.current === data.task_id) {
                  setConversationItems(items => {
                    const last = items[items.length - 1]
                    if (last?.type === 'thinking' && last.thinking?.isStreaming) {
                      return [...items.slice(0, -1), {
                        ...last,
                        thinking: { content: data.content, isStreaming: false },
                      }]
                    }
                    return [...items, {
                      id: `thinking-${uuid()}`,
                      type: 'thinking' as const,
                      timestamp: new Date().toISOString(),
                      thinking: { content: data.content, isStreaming: false },
                    }]
                  })
                }
              } else {
                // agent_event, system → debug only
                setAgentMessages(p => [...p, { role: data.role, content: data.content }])
              }
            }
            return prev
          })
        }
        if (data.type === 'task_todos') {
          setSelectedTask(prev => {
            if (prev && prev.id === data.task_id) setTodoItems(data.todos || [])
            return prev
          })
        }
        if (data.type === 'task_questions') {
          setSelectedTask(prev => {
            if (prev && prev.id === data.task_id) {
              setPendingQuestions({ request_id: data.request_id, questions: data.questions || [] })
              setSelectedAnswers({})
              setOtherInputs({})
              setShowOtherInput({})
              // Add question item to conversation
              if (selectedTaskIdRef.current === data.task_id) {
                setConversationItems(items => [...items, {
                  id: `question-${data.request_id}`,
                  type: 'question' as const,
                  timestamp: new Date().toISOString(),
                  questions: { request_id: data.request_id, questions: data.questions || [] },
                }])
              }
              setTimeout(() => questionBannerRef.current?.scrollIntoView({ behavior: 'smooth' }), 100)
            }
            return prev
          })
        }
        if (data.type === 'schedule_triggered') {
          loadSchedulesRef.current?.()
          loadScheduleTasksRef.current?.()
        }
        if (data.type === 'fanout_triggered') {
          loadSchedulesRef.current?.()
          loadTasksRef.current?.()
          loadScheduleTasksRef.current?.()
        }
        // Plan-lifecycle events: plan committed, aborted, or invalidated.
        // Without these the left-panel badge (ScheduleList.PlanStatusBadge)
        // and the SchedulePlanPanel remain stale — the API query that
        // hydrates `schedules` isn't re-run, so plan_status='planning'
        // sticks on-screen even after the DB row flipped to 'ready'.
        if (
          data.type === 'schedule_plan_ready' ||
          data.type === 'schedule_plan_timeout' ||
          data.type === 'schedule_plan_stale'
        ) {
          loadSchedulesRef.current?.()
          loadScheduleTasksRef.current?.()
        }
        // Workspace assistant events
        if (data.type === 'ws_assistant_message' && setWsAssistantMessages) {
          if (data.role === 'delta') {
            setWsAssistantMessages(p => {
              const last = p[p.length - 1]
              if (last && last.role === '_streaming') {
                return [...p.slice(0, -1), { role: '_streaming', content: last.content + data.content }]
              }
              return [...p, { role: '_streaming', content: data.content }]
            })
          } else if (data.role === 'assistant') {
            setWsAssistantMessages(p => {
              const last = p[p.length - 1]
              if (last && last.role === '_streaming') {
                return [...p.slice(0, -1), { role: data.role, content: data.content }]
              }
              return [...p, { role: data.role, content: data.content }]
            })
          } else {
            setWsAssistantMessages(p => [...p, { role: data.role, content: data.content }])
          }
        }
        if (data.type === 'ws_assistant_done') {
          if (setWsAssistantRunning) setWsAssistantRunning(false)
          if (loadWsStructured) loadWsStructured()
        }
        if (data.type === 'agent_done') {
          setSelectedTask(prev => {
            if (!prev || prev.id !== data.task_id) return prev
            setPendingQuestions(null)
            setSelectedAnswers({})
            setOtherInputs({})
            setShowOtherInput({})
            // Refresh task state for both success and failure
            api.get(`/api/tasks/${data.task_id}`).then((t: Task) => {
              setSelectedTask(t)
              setTasks(p => p.map(pt => pt.id === t.id ? t : pt))
              if (setScheduleTasks) {
                setScheduleTasks(p => p.map(pt => pt.id === t.id ? t : pt))
              }
              // Update current plan card content if plan changed
              if (t.plan && selectedTaskIdRef.current === data.task_id) {
                setConversationItems(items => {
                  const currentPlan = items.find(i => i.type === 'plan' && i.plan?.isCurrent)
                  if (currentPlan && currentPlan.plan?.content !== t.plan) {
                    return items.map(i =>
                      i.id === currentPlan.id
                        ? { ...i, plan: { ...i.plan!, content: t.plan! } }
                        : i
                    )
                  }
                  return items
                })
              }
            }).catch(() => {})
            return prev
          })
        }
      } catch { /* ignore */ }
    }
    return () => es.close()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appView])
}

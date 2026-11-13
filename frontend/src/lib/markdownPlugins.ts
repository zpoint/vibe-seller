import remarkGfm from 'remark-gfm'

/**
 * The remark config shared by EVERY markdown renderer in the app —
 * the task conversation, plan cards, questions, workspace viewers and
 * the workspace assistant.
 *
 * GFM (tables, strikethrough, autolinks, task lists) is part of what
 * the agents actually emit: an assistant answer with a `| a | b |`
 * table renders as raw text when its call site forgot this config
 * (the workspace assistant's bug). One config, imported everywhere —
 * never re-list plugins at a call site.
 */
export const REMARK_PLUGINS = [remarkGfm]

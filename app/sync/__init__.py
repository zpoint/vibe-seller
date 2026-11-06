"""Central sync scheduler package.

Import from the modules directly (``app.sync.scheduler``,
``app.sync.jobs``, ``app.sync.state``) — this package deliberately
re-exports nothing, so that ``app.sync.*`` leaf modules (like
``github``) can be imported from the workspace sync managers without
walking into the scheduler's import graph.
"""

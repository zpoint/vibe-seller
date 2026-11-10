import { useState } from 'react'

import {
  AUTO_EXPAND_LIMIT,
  buildFileTree,
  countEntries,
  type FileTreeNode,
  type TaskFileEntry,
} from './fileTree'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function FileTreeItem({
  node,
  taskId,
  depth,
  defaultExpanded,
}: {
  node: FileTreeNode
  taskId: string
  depth: number
  defaultExpanded: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const paddingLeft = `${depth * 16 + 8}px`

  if (node.isDir) {
    return (
      <div>
        <button
          data-testid={`folder-${node.fullPath}`}
          aria-expanded={expanded}
          onClick={() => setExpanded(p => !p)}
          className="flex items-center gap-1.5 w-full px-2 py-1 text-xs text-gray-600 hover:bg-gray-100 rounded transition-colors cursor-pointer"
          style={{ paddingLeft }}
        >
          <span className="text-[10px] w-3 text-center">{expanded ? '▾' : '▸'}</span>
          <span className="text-gray-400">{'📁'}</span>
          <span className="font-medium">{node.name}/</span>
          <span className="text-gray-400 ml-auto">{node.children.length}</span>
        </button>
        {expanded && node.children.map(child => (
          <FileTreeItem
            key={child.fullPath}
            node={child}
            taskId={taskId}
            depth={depth + 1}
            defaultExpanded={defaultExpanded}
          />
        ))}
      </div>
    )
  }

  const encodedPath = node.fullPath.split('/').map(encodeURIComponent).join('/')
  return (
    <a
      data-testid={`file-${node.fullPath}`}
      href={`/api/tasks/${taskId}/files/${encodedPath}`}
      download={node.name}
      className="flex items-center gap-1.5 px-2 py-1 text-xs hover:bg-indigo-50 rounded transition-colors"
      style={{ paddingLeft: `${(depth) * 16 + 8 + 12 + 6}px` }}
    >
      <span className="text-gray-400 text-[10px]">
        {node.type?.includes('pdf') ? '📄' : '📎'}
      </span>
      <span className="text-indigo-600 font-medium truncate">{node.name}</span>
      <span className="text-gray-400 ml-auto whitespace-nowrap">
        {node.size != null ? formatSize(node.size) : ''}
      </span>
    </a>
  )
}

/** The task's files as a tree; folders start open only for small trees. */
export function TaskFileTree({ taskId, files }: { taskId: string; files: TaskFileEntry[] }) {
  const tree = buildFileTree(files)
  if (tree.length === 0) return null
  const defaultExpanded = countEntries(tree) <= AUTO_EXPAND_LIMIT
  return (
    <div className="space-y-0.5" data-testid="file-tree">
      {tree.map(node => (
        <FileTreeItem
          key={node.fullPath}
          node={node}
          taskId={taskId}
          depth={0}
          defaultExpanded={defaultExpanded}
        />
      ))}
    </div>
  )
}

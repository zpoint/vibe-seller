export interface TaskFileEntry {
  name: string
  size: number
  type: string
  modified_at: string
}

export interface FileTreeNode {
  name: string
  fullPath: string
  isDir: boolean
  size?: number
  type?: string
  children: FileTreeNode[]
}

// Above this many entries (files + folders, all levels), folders start
// collapsed: an ad audit leaves hundreds of per-campaign files, and an
// open tree buries the report under them. The user opens what they need.
export const AUTO_EXPAND_LIMIT = 20

export function buildFileTree(files: TaskFileEntry[]): FileTreeNode[] {
  const root: FileTreeNode = { name: '', fullPath: '', isDir: true, children: [] }
  for (const f of files) {
    const parts = f.name.split('/')
    let node = root
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i]
      const isLast = i === parts.length - 1
      let child = node.children.find(c => c.name === part)
      if (!child) {
        child = {
          name: part,
          fullPath: parts.slice(0, i + 1).join('/'),
          isDir: !isLast,
          size: isLast ? f.size : undefined,
          type: isLast ? f.type : undefined,
          children: [],
        }
        node.children.push(child)
      }
      node = child
    }
  }
  return root.children
}

/** Every file and folder in the tree, at every level. */
export function countEntries(nodes: FileTreeNode[]): number {
  return nodes.reduce((n, node) => n + 1 + countEntries(node.children), 0)
}

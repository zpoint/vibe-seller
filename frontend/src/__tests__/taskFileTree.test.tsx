/**
 * Tests for TaskFiles file tree display.
 * Covers: tree building from flat paths, folder expand/collapse,
 * download links, nested directories.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'

// Minimal types matching the real component
interface TaskFileEntry {
  name: string
  size: number
  type: string
  modified_at: string
}

interface FileTreeNode {
  name: string
  fullPath: string
  isDir: boolean
  size?: number
  type?: string
  children: FileTreeNode[]
}

// Copy of buildFileTree from ConversationStream
function buildFileTree(files: TaskFileEntry[]): FileTreeNode[] {
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

// Standalone tree component for testing
function FileTreeItem({ node, taskId, depth }: { node: FileTreeNode; taskId: string; depth: number }) {
  const [expanded, setExpanded] = useState(true)

  if (node.isDir) {
    return (
      <div>
        <button
          data-testid={`folder-${node.fullPath}`}
          onClick={() => setExpanded(p => !p)}
        >
          {expanded ? 'v' : '>'} {node.name}/
        </button>
        {expanded && node.children.map(child => (
          <FileTreeItem key={child.fullPath} node={child} taskId={taskId} depth={depth + 1} />
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
    >
      {node.name} ({node.size} bytes)
    </a>
  )
}

function TestTaskFiles({ taskId, files }: { taskId: string; files: TaskFileEntry[] }) {
  const tree = buildFileTree(files)
  if (tree.length === 0) return null
  return (
    <div data-testid="file-tree">
      {tree.map(node => (
        <FileTreeItem key={node.fullPath} node={node} taskId={taskId} depth={0} />
      ))}
    </div>
  )
}

const TASK_ID = 'test-task-123'

describe('buildFileTree', () => {
  it('builds flat files at root level', () => {
    const files: TaskFileEntry[] = [
      { name: 'report.csv', size: 100, type: 'text/csv', modified_at: '' },
      { name: 'data.json', size: 200, type: 'application/json', modified_at: '' },
    ]
    const tree = buildFileTree(files)
    expect(tree).toHaveLength(2)
    expect(tree[0].name).toBe('report.csv')
    expect(tree[0].isDir).toBe(false)
    expect(tree[1].name).toBe('data.json')
  })

  it('builds single-level subdirectory', () => {
    const files: TaskFileEntry[] = [
      { name: 'invoices/INV-001.pdf', size: 1000, type: 'application/pdf', modified_at: '' },
      { name: 'invoices/INV-002.pdf', size: 2000, type: 'application/pdf', modified_at: '' },
    ]
    const tree = buildFileTree(files)
    expect(tree).toHaveLength(1)
    expect(tree[0].name).toBe('invoices')
    expect(tree[0].isDir).toBe(true)
    expect(tree[0].children).toHaveLength(2)
    expect(tree[0].children[0].name).toBe('INV-001.pdf')
    expect(tree[0].children[0].fullPath).toBe('invoices/INV-001.pdf')
  })

  it('builds nested subdirectories', () => {
    const files: TaskFileEntry[] = [
      { name: 'invoices/SA/INV-SA-001.pdf', size: 500, type: 'application/pdf', modified_at: '' },
      { name: 'invoices/AE/INV-AE-001.pdf', size: 600, type: 'application/pdf', modified_at: '' },
    ]
    const tree = buildFileTree(files)
    expect(tree).toHaveLength(1)
    const invoices = tree[0]
    expect(invoices.children).toHaveLength(2)
    expect(invoices.children[0].name).toBe('SA')
    expect(invoices.children[0].isDir).toBe(true)
    expect(invoices.children[0].children[0].name).toBe('INV-SA-001.pdf')
  })

  it('mixes top-level files and directories', () => {
    const files: TaskFileEntry[] = [
      { name: 'summary.txt', size: 50, type: 'text/plain', modified_at: '' },
      { name: 'invoices/INV-001.pdf', size: 1000, type: 'application/pdf', modified_at: '' },
    ]
    const tree = buildFileTree(files)
    expect(tree).toHaveLength(2)
    expect(tree[0].name).toBe('summary.txt')
    expect(tree[0].isDir).toBe(false)
    expect(tree[1].name).toBe('invoices')
    expect(tree[1].isDir).toBe(true)
  })

  it('returns empty for empty input', () => {
    expect(buildFileTree([])).toHaveLength(0)
  })
})

describe('FileTree rendering', () => {
  it('renders top-level files with download links', () => {
    const files: TaskFileEntry[] = [
      { name: 'report.csv', size: 100, type: 'text/csv', modified_at: '' },
    ]
    render(<TestTaskFiles taskId={TASK_ID} files={files} />)
    const link = screen.getByTestId('file-report.csv')
    expect(link).toHaveAttribute('href', `/api/tasks/${TASK_ID}/files/report.csv`)
    expect(link).toHaveAttribute('download', 'report.csv')
  })

  it('renders folders with expand/collapse', () => {
    const files: TaskFileEntry[] = [
      { name: 'invoices/INV-001.pdf', size: 1000, type: 'application/pdf', modified_at: '' },
    ]
    render(<TestTaskFiles taskId={TASK_ID} files={files} />)

    // Folder visible and expanded by default
    const folder = screen.getByTestId('folder-invoices')
    expect(folder).toBeInTheDocument()

    // File visible inside folder
    const file = screen.getByTestId('file-invoices/INV-001.pdf')
    expect(file).toBeInTheDocument()

    // Collapse folder
    fireEvent.click(folder)
    expect(screen.queryByTestId('file-invoices/INV-001.pdf')).not.toBeInTheDocument()

    // Expand again
    fireEvent.click(folder)
    expect(screen.getByTestId('file-invoices/INV-001.pdf')).toBeInTheDocument()
  })

  it('renders nested directories', () => {
    const files: TaskFileEntry[] = [
      { name: 'invoices/SA/INV-001.pdf', size: 500, type: 'application/pdf', modified_at: '' },
      { name: 'invoices/AE/INV-002.pdf', size: 600, type: 'application/pdf', modified_at: '' },
    ]
    render(<TestTaskFiles taskId={TASK_ID} files={files} />)

    expect(screen.getByTestId('folder-invoices')).toBeInTheDocument()
    expect(screen.getByTestId('folder-invoices/SA')).toBeInTheDocument()
    expect(screen.getByTestId('folder-invoices/AE')).toBeInTheDocument()
    expect(screen.getByTestId('file-invoices/SA/INV-001.pdf')).toBeInTheDocument()
    expect(screen.getByTestId('file-invoices/AE/INV-002.pdf')).toBeInTheDocument()
  })

  it('encodes path segments in download URL', () => {
    const files: TaskFileEntry[] = [
      { name: 'reports/Q1 2026/summary.csv', size: 100, type: 'text/csv', modified_at: '' },
    ]
    render(<TestTaskFiles taskId={TASK_ID} files={files} />)
    const link = screen.getByTestId('file-reports/Q1 2026/summary.csv')
    expect(link).toHaveAttribute('href', `/api/tasks/${TASK_ID}/files/reports/Q1%202026/summary.csv`)
  })

  it('returns null for empty files', () => {
    const { container } = render(<TestTaskFiles taskId={TASK_ID} files={[]} />)
    expect(container.innerHTML).toBe('')
  })
})

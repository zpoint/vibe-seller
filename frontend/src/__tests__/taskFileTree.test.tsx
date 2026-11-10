/**
 * Tests for TaskFiles file tree display.
 * Covers: tree building from flat paths, folder expand/collapse,
 * download links, nested directories, and folders starting collapsed
 * once the tree holds more than AUTO_EXPAND_LIMIT entries.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import {
  AUTO_EXPAND_LIMIT,
  buildFileTree,
  countEntries,
  type TaskFileEntry,
} from '../components/conversation/fileTree'
import { TaskFileTree } from '../components/conversation/TaskFileTree'

// The real component (not a copy), so these tests break when it does.
function TestTaskFiles({ taskId, files }: { taskId: string; files: TaskFileEntry[] }) {
  return <TaskFileTree taskId={taskId} files={files} />
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
      { name: 'invoices/US/INV-US-001.pdf', size: 500, type: 'application/pdf', modified_at: '' },
      { name: 'invoices/UK/INV-UK-001.pdf', size: 600, type: 'application/pdf', modified_at: '' },
    ]
    const tree = buildFileTree(files)
    expect(tree).toHaveLength(1)
    const invoices = tree[0]
    expect(invoices.children).toHaveLength(2)
    expect(invoices.children[0].name).toBe('US')
    expect(invoices.children[0].isDir).toBe(true)
    expect(invoices.children[0].children[0].name).toBe('INV-US-001.pdf')
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
      { name: 'invoices/US/INV-001.pdf', size: 500, type: 'application/pdf', modified_at: '' },
      { name: 'invoices/UK/INV-002.pdf', size: 600, type: 'application/pdf', modified_at: '' },
    ]
    render(<TestTaskFiles taskId={TASK_ID} files={files} />)

    expect(screen.getByTestId('folder-invoices')).toBeInTheDocument()
    expect(screen.getByTestId('folder-invoices/US')).toBeInTheDocument()
    expect(screen.getByTestId('folder-invoices/UK')).toBeInTheDocument()
    expect(screen.getByTestId('file-invoices/US/INV-001.pdf')).toBeInTheDocument()
    expect(screen.getByTestId('file-invoices/UK/INV-002.pdf')).toBeInTheDocument()
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

function entries(n: number, dir = 'out'): TaskFileEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    name: `${dir}/campaign-${i}.tsv`,
    size: 10,
    type: 'text/tab-separated-values',
    modified_at: '',
  }))
}

describe('Folders start collapsed in a large tree', () => {
  it('counts files and folders at every level', () => {
    const tree = buildFileTree([
      { name: 'a.md', size: 1, type: 'text/markdown', modified_at: '' },
      ...entries(2, 'out/amazon_AE'),
    ])
    // a.md, out/, out/amazon_AE/, two files
    expect(countEntries(tree)).toBe(5)
  })

  it('keeps folders open at the limit', () => {
    // one folder + (LIMIT - 1) files = LIMIT entries
    render(<TestTaskFiles taskId={TASK_ID} files={entries(AUTO_EXPAND_LIMIT - 1)} />)
    expect(screen.getByTestId('folder-out')).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('file-out/campaign-0.tsv')).toBeInTheDocument()
  })

  it('collapses every folder above the limit; a click opens one', () => {
    const files = [
      { name: 'REPORT.md', size: 1, type: 'text/markdown', modified_at: '' },
      ...entries(AUTO_EXPAND_LIMIT, 'out/amazon_AE'),
    ]
    render(<TestTaskFiles taskId={TASK_ID} files={files} />)
    // Top-level files stay visible; folders are closed.
    expect(screen.getByTestId('file-REPORT.md')).toBeInTheDocument()
    const out = screen.getByTestId('folder-out')
    expect(out).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('folder-out/amazon_AE')).not.toBeInTheDocument()

    fireEvent.click(out)
    const ae = screen.getByTestId('folder-out/amazon_AE')
    expect(ae).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('file-out/amazon_AE/campaign-0.tsv')).not.toBeInTheDocument()
    fireEvent.click(ae)
    expect(screen.getByTestId('file-out/amazon_AE/campaign-0.tsv')).toBeInTheDocument()
  })
})

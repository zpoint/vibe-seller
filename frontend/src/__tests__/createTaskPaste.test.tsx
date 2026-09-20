/**
 * The New Task dialog's attachment box says "paste" — so paste has to
 * work from wherever focus happens to be.
 *
 * The bug this pins: `onPaste` sat on the dialog's overlay <div>. A
 * paste event is dispatched to the FOCUSED element, and React 18
 * delegates from the root container (`#root`, see main.tsx). Click the
 * dashed drop zone — a non-focusable <div>, and the one spot the copy
 * tells you to paste into — and focus falls back to `document.body`,
 * whose event path (body -> html -> document) never crosses `#root`.
 * No React `onPaste` in the tree ran, and Ctrl+V did nothing.
 *
 * Also pinned: a file the dialog refuses has to SAY so. Silently
 * skipping it is indistinguishable from a dead drop zone.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { CreateTaskModal } from '../components/CreateTaskModal'

const makeI18n = () => {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: {
      en: { translation: enTranslation },
      zh: { translation: zhTranslation },
    },
    lng: 'en',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })
  return inst
}

function pngFile(name: string, size = 1024): File {
  const f = new File([new Uint8Array(8)], name, { type: 'image/png' })
  Object.defineProperty(f, 'size', { value: size })
  return f
}

/** A clipboard carrying files, the shape the browser hands a paste. */
function clipboardEvent(files: File[]): Event {
  const ev = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'clipboardData', {
    value: {
      items: files.map(f => ({
        kind: 'file',
        type: f.type,
        getAsFile: () => f,
      })),
    },
  })
  return ev
}

function renderModal(over: { onSubmit?: (...a: unknown[]) => Promise<void> } = {}) {
  return render(
    <I18nextProvider i18n={makeI18n()}>
      <CreateTaskModal
        showAllTasks={false}
        storeName="acme"
        selectedStore={null}
        onClose={vi.fn()}
        onSubmit={over.onSubmit ?? vi.fn(async () => {})}
      />
    </I18nextProvider>,
  )
}

/** A drag event carrying files, or carrying only text. */
function dragEvent(kind: 'drop' | 'dragover', files: File[], types?: string[]): Event {
  const ev = new Event(kind, { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'dataTransfer', {
    value: { files, types: types ?? (files.length ? ['Files'] : []) },
  })
  return ev
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:preview')
  URL.revokeObjectURL = vi.fn()
})

describe('New Task dialog: pasting an image', () => {
  it('attaches an image pasted with focus on document.body', () => {
    renderModal()
    // Exactly what a click on the dashed drop zone leaves behind.
    expect(document.activeElement === document.body || document.activeElement)
      .toBeTruthy()
    fireEvent(document.body, clipboardEvent([pngFile('shot.png')]))
    expect(screen.getByAltText('shot.png')).toBeInTheDocument()
  })

  it('attaches an image pasted into the description textarea', () => {
    renderModal()
    const desc = screen.getByLabelText(/description/i)
    fireEvent(desc, clipboardEvent([pngFile('typed.png')]))
    expect(screen.getByAltText('typed.png')).toBeInTheDocument()
  })

  it('attaches a pasted screenshot exactly once', () => {
    renderModal()
    fireEvent(document.body, clipboardEvent([pngFile('once.png')]))
    expect(screen.getAllByAltText('once.png')).toHaveLength(1)
  })

  it('stands down when a deeper handler already consumed the paste', () => {
    renderModal()
    const desc = screen.getByLabelText(/description/i)
    // A composer-style handler that stages the file itself.
    desc.addEventListener('paste', e => e.preventDefault())
    fireEvent(desc, clipboardEvent([pngFile('claimed.png')]))
    expect(screen.queryByAltText('claimed.png')).not.toBeInTheDocument()
  })

  it('leaves a plain-text paste alone', () => {
    renderModal()
    const ev = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(ev, 'clipboardData', {
      value: { items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }] },
    })
    fireEvent(screen.getByLabelText(/title/i), ev)
    // Swallowing this would stop the user typing a title by pasting.
    expect(ev.defaultPrevented).toBe(false)
  })

  it('stops listening once the dialog closes', () => {
    const { unmount } = renderModal()
    unmount()
    // Must not throw, and must not resurrect the unmounted dialog.
    fireEvent(document.body, clipboardEvent([pngFile('late.png')]))
    expect(screen.queryByAltText('late.png')).not.toBeInTheDocument()
  })
})

describe('New Task dialog: a file the box refuses', () => {
  it('says why an oversized file was not attached', () => {
    renderModal()
    fireEvent(document.body, clipboardEvent([pngFile('huge.png', 20 * 1024 * 1024)]))
    expect(screen.queryByAltText('huge.png')).not.toBeInTheDocument()
    expect(screen.getByTestId('attachment-rejected')).toHaveTextContent(
      /huge\.png.*10MB/,
    )
  })

  it('says why an unsupported type was not attached', () => {
    renderModal()
    const bmp = new File([new Uint8Array(8)], 'old.bmp', { type: 'image/bmp' })
    fireEvent(document.body, clipboardEvent([bmp]))
    expect(screen.getByTestId('attachment-rejected')).toHaveTextContent(
      /old\.bmp.*unsupported/i,
    )
  })

  it('names a nameless clipboard image rather than blaming ""', () => {
    renderModal()
    const nameless = new File([new Uint8Array(8)], '', { type: 'image/bmp' })
    fireEvent(document.body, clipboardEvent([nameless]))
    expect(screen.getByTestId('attachment-rejected')).toHaveTextContent(
      /Clipboard item/,
    )
  })

  it('keeps the good file from a mixed batch and flags only the bad one', () => {
    renderModal()
    fireEvent(document.body, clipboardEvent([
      pngFile('good.png'),
      pngFile('huge.png', 20 * 1024 * 1024),
    ]))
    expect(screen.getByAltText('good.png')).toBeInTheDocument()
    expect(screen.getByTestId('attachment-rejected')).toHaveTextContent('huge.png')
  })

  it('clears the notice once a later batch is accepted', () => {
    renderModal()
    fireEvent(document.body, clipboardEvent([pngFile('huge.png', 20 * 1024 * 1024)]))
    expect(screen.getByTestId('attachment-rejected')).toBeInTheDocument()
    fireEvent(document.body, clipboardEvent([pngFile('fine.png')]))
    expect(screen.queryByTestId('attachment-rejected')).not.toBeInTheDocument()
  })
})

describe('New Task dialog: what actually gets uploaded', () => {
  it('gives a nameless clipboard image a real filename, not just a label', async () => {
    const onSubmit = vi.fn<(...args: unknown[]) => Promise<void>>(async () => {})
    renderModal({ onSubmit })
    const nameless = new File([new Uint8Array(8)], '', { type: 'image/png' })
    fireEvent(document.body, clipboardEvent([nameless]))

    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: 'T' } })
    fireEvent.click(screen.getByRole('button', { name: /create/i }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())

    // The server derives the stored extension from File.name alone
    // (app/routers/attachments.py), so a display-only label would
    // still land the screenshot in the agent's cwd as `upload.bin`.
    const pending = onSubmit.mock.calls[0][2] as Array<{ file: File; name: string }>
    expect(pending[0].file.name).toBe('pasted.png')
    expect(pending[0].name).toBe('pasted.png')
  })

  it('leaves an already-named file alone', async () => {
    const onSubmit = vi.fn<(...args: unknown[]) => Promise<void>>(async () => {})
    renderModal({ onSubmit })
    fireEvent(document.body, clipboardEvent([pngFile('report.png')]))
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: 'T' } })
    fireEvent.click(screen.getByRole('button', { name: /create/i }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    const pending = onSubmit.mock.calls[0][2] as Array<{ file: File }>
    expect(pending[0].file.name).toBe('report.png')
  })
})

describe('New Task dialog: a file dropped beside the box', () => {
  it('attaches instead of letting the browser navigate away', () => {
    const { container } = renderModal()
    const overlay = container.firstElementChild as HTMLElement
    const ev = dragEvent('drop', [pngFile('dropped.png')])
    fireEvent(overlay, ev)
    expect(ev.defaultPrevented).toBe(true)
    expect(screen.getByAltText('dropped.png')).toBeInTheDocument()
  })

  it('attaches a file dropped on the zone exactly once', () => {
    renderModal()
    const zone = screen.getByText(/drop images here/i).parentElement as HTMLElement
    fireEvent(zone, dragEvent('drop', [pngFile('zone.png')]))
    expect(screen.getAllByAltText('zone.png')).toHaveLength(1)
  })

  it('leaves a text drag into the description to the browser', () => {
    renderModal()
    const desc = screen.getByLabelText(/description/i)
    const ev = dragEvent('drop', [], ['text/plain'])
    fireEvent(desc, ev)
    // Cancelling this would silently eat dragged text — the dialog
    // only has business claiming drags that carry files.
    expect(ev.defaultPrevented).toBe(false)
  })

  it('does not claim a text drag on dragover either', () => {
    renderModal()
    const zone = screen.getByText(/drop images here/i).parentElement as HTMLElement
    const text = dragEvent('dragover', [], ['text/plain'])
    fireEvent(zone, text)
    expect(text.defaultPrevented).toBe(false)

    const withFile = dragEvent('dragover', [], ['Files'])
    fireEvent(zone, withFile)
    expect(withFile.defaultPrevented).toBe(true)
  })
})

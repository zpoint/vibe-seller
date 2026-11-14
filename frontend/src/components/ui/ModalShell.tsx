/**
 * The centred overlay both update dialogs sit in.
 *
 * Shared because the "已更新到" panel and the "有可用更新" dialog are
 * the same surface at different moments of one flow — two copies of the
 * scrim and the max-height card is how the two start looking different.
 */
export function ModalShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-lg bg-white shadow-xl">
        {children}
      </div>
    </div>
  )
}

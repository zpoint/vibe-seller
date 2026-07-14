/** Hamburger that opens the mobile nav drawer. Shared by the tasks,
 *  workspace and settings headers so the icon markup lives in one place. */
export function MobileMenuButton({ onClick, label, className = '' }: {
  onClick: () => void
  label: string
  className?: string
}) {
  return (
    <button
      onClick={onClick}
      className={`w-9 h-9 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 flex-shrink-0 ${className}`}
      aria-label={label}
    >
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
      </svg>
    </button>
  )
}

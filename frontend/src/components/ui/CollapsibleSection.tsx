import { useState, useEffect } from 'react'

export function CollapsibleSection({ heading, defaultExpanded, children }: { heading: string; defaultExpanded: boolean; children: React.ReactNode }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  // Update when defaultExpanded changes (e.g., task status changes)
  useEffect(() => {
    setExpanded(defaultExpanded)
  }, [defaultExpanded])
  return (
    <div className="px-3 py-2 border-b border-gray-100">
      {heading && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 w-full text-left group"
        >
          <svg
            className={`w-3.5 h-3.5 text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{heading}</h4>
        </button>
      )}
      {expanded && <div className="mt-1">{children}</div>}
    </div>
  )
}

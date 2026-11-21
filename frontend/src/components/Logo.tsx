/**
 * nextrmnl mark: a terminal window with a prompt and a cursor, in violet. The source of every version is
 * nexapps/icons-vergleich/neu.py; the same shape is in public/logo.svg (favicon) and scripts/app-icons.mjs (home
 * screen). The colours come from styles/index.css (--nt-logo-…) and follow the theme: a dark tile in dark mode, a
 * white one in light mode.
 */
export function Logo({ className = 'h-8 w-8', withWordmark = false }: { className?: string; withWordmark?: boolean }) {
  // userSpaceOnUse, so the gradient runs across the whole mark instead of restarting for each stroke.
  const line = { stroke: 'url(#nextrmnl-mark)' }
  const mark = (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="nextrmnl-mark" gradientUnits="userSpaceOnUse" x1="10" y1="10" x2="54" y2="54">
          <stop offset="0" style={{ stopColor: 'var(--nt-logo-verlauf-1)' }} />
          <stop offset=".5" style={{ stopColor: 'var(--nt-logo-verlauf-2)' }} />
          <stop offset="1" style={{ stopColor: 'var(--nt-logo-verlauf-3)' }} />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="16" style={{ fill: 'var(--nt-logo-grund)' }} />
      <rect
        x="2.75"
        y="2.75"
        width="58.5"
        height="58.5"
        rx="15.3"
        fill="none"
        strokeWidth="1.5"
        style={{ stroke: 'var(--nt-logo-rand)', strokeOpacity: 'var(--nt-logo-rand-deckung)' }}
      />
      <rect x="10" y="13" width="44" height="38" rx="5.5" fill="none" strokeWidth="4.2" {...line} />
      <path d="M10 22h44" strokeWidth="3" strokeOpacity=".55" {...line} />
      <circle cx="16" cy="17.6" r="1.7" fill="url(#nextrmnl-mark)" />
      <circle cx="21.5" cy="17.6" r="1.7" fill="url(#nextrmnl-mark)" />
      <circle cx="27" cy="17.6" r="1.7" fill="url(#nextrmnl-mark)" />
      <path d="M18 30 24.5 36 18 42" fill="none" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" {...line} />
      <path d="M29 42h11" strokeWidth="4" strokeLinecap="round" style={{ stroke: 'var(--nt-logo-cursor)' }} />
    </svg>
  )
  if (!withWordmark) return mark
  return (
    <span className="flex items-center gap-2.5">
      {mark}
      <span className="hidden text-lg font-bold tracking-tight sm:inline">
        NEX<span className="text-accent-500">TRMNL</span>
      </span>
    </span>
  )
}

/** nextrmnl mark: prompt character and cursor in violet, within the style of the nexapps marks. */
export function Logo({ className = 'h-8 w-8', withWordmark = false }: { className?: string; withWordmark?: boolean }) {
  // userSpaceOnUse, so the gradient runs across the whole mark instead of restarting for each stroke.
  const mark = (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="nextrmnl-mark" gradientUnits="userSpaceOnUse" x1="8" y1="8" x2="56" y2="56">
          <stop offset="0" stopColor="#ede9fe" />
          <stop offset=".5" stopColor="#a78bfa" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="16" fill="#110d1a" />
      <rect x="2" y="2" width="60" height="60" rx="16" fill="none" stroke="url(#nextrmnl-mark)" strokeWidth="2.5" strokeOpacity=".55" />
      <path d="M17 21.5 27.5 32 17 42.5" fill="none" stroke="url(#nextrmnl-mark)" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M33 43h14" fill="none" stroke="#a78bfa" strokeWidth="5" strokeLinecap="round" />
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

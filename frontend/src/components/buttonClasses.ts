export type ButtonVariant = 'primary' | 'ghost' | 'danger' | 'link'

/** The look of a button, also for a link that should look like a button (for example a download). */
export function buttonClasses(variant: ButtonVariant = 'primary', size: 'md' | 'sm' = 'md'): string {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-full font-semibold transition-colors ' +
    'disabled:cursor-not-allowed disabled:opacity-60 ' +
    (size === 'sm' ? 'px-3.5 py-1.5 text-xs ' : 'px-5 py-2.5 text-sm ')
  const styles = {
    primary: 'bg-accent-500 text-on-accent hover:bg-accent-400 shadow-lg shadow-accent-700/25',
    danger: 'border border-bad-500/40 bg-bad-500/10 text-bad-500 hover:bg-bad-500/20',
    ghost: 'border border-ink-700 bg-ink-850 text-mist-300 hover:bg-ink-800 hover:text-mist-100',
    link: 'text-mist-500 hover:text-mist-100',
  }[variant]
  return `${base} ${styles}`
}

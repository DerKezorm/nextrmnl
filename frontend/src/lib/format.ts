/** Numbers and times in the configured language. The language comes from i18next, not from the browser. */

import i18n from '../i18n'

function locale(): string {
  return i18n.language || 'en'
}

export function formatDateTime(value: string | Date): string {
  return new Date(value).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' })
}

export function formatDate(value: string | Date): string {
  return new Date(value).toLocaleDateString(locale(), { dateStyle: 'medium' })
}

export function formatNumber(value: number, maximumFractionDigits = 1): string {
  return value.toLocaleString(locale(), { maximumFractionDigits })
}

/** "1 h 04 min", "22 min", "10 s": short and readable in every language. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  return `${hours} h ${String(minutes % 60).padStart(2, '0')} min`
}

const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 86400],
  ['month', 30 * 86400],
  ['week', 7 * 86400],
  ['day', 86400],
  ['hour', 3600],
  ['minute', 60],
]

/** "12 minutes ago", "yesterday", "now": Intl picks the words, we only pick the unit. */
export function formatRelative(value: string | Date, now: Date = new Date()): string {
  const seconds = Math.round((new Date(value).getTime() - now.getTime()) / 1000)
  const format = new Intl.RelativeTimeFormat(locale(), { numeric: 'auto' })
  for (const [unit, size] of STEPS) {
    if (Math.abs(seconds) >= size) return format.format(Math.trunc(seconds / size), unit)
  }
  return format.format(0, 'second')
}

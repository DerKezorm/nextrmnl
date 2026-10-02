/**
 * Ends of guest accounts and shares. People pick a day; the end is the last second of that day in their own
 * time zone, sent with the zone so the server knows exactly when.
 */

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** `YYYY-MM-DD` of a moment, in local time, for a date field. Empty for none. */
export function toDateInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** The last second of a picked day, as ISO with time zone. Null for an empty field. */
export function endOfDay(day: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!match) return null
  const end = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 23, 59, 59)
  return Number.isNaN(end.getTime()) ? null : end.toISOString()
}

/** Today as `YYYY-MM-DD`, the earliest day a date field offers. */
export function todayInput(now: Date = new Date()): string {
  return toDateInput(now.toISOString())
}

export function isPast(iso: string | null | undefined, now: Date = new Date()): boolean {
  return Boolean(iso) && new Date(iso as string).getTime() <= now.getTime()
}

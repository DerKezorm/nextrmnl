import { endOfDay, isPast, toDateInput, todayInput } from './dates'

describe('ends picked as a day', () => {
  it('ends on the last second of the picked day, local time, with a zone', () => {
    const iso = endOfDay('2026-10-12')
    expect(iso).toMatch(/Z$/)
    const end = new Date(iso as string)
    expect([end.getFullYear(), end.getMonth() + 1, end.getDate(), end.getHours(), end.getMinutes(), end.getSeconds()]).toEqual([2026, 10, 12, 23, 59, 59])
    expect(toDateInput(iso)).toBe('2026-10-12')
  })

  it('treats empty and odd input as no end', () => {
    expect(endOfDay('')).toBeNull()
    expect(endOfDay('12.10.2026')).toBeNull()
    expect(toDateInput(null)).toBe('')
    expect(toDateInput('not a date')).toBe('')
  })

  it('knows past from future', () => {
    const now = new Date('2026-10-02T12:00:00Z')
    expect(isPast('2026-10-02T11:59:59Z', now)).toBe(true)
    expect(isPast('2026-10-02T12:00:01Z', now)).toBe(false)
    expect(isPast(null, now)).toBe(false)
    expect(todayInput(new Date(2026, 9, 2, 10))).toBe('2026-10-02')
  })
})

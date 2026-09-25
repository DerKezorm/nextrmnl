/**
 * There is no fallback language. If a text is missing in a language, the raw key would show there.
 * That is why all language files must have exactly the same keys. New language files in this
 * folder are checked automatically along with the rest.
 */

import { LANGUAGES } from './index'

const files = import.meta.glob('./*.json', { eager: true, import: 'default' }) as Record<string, Record<string, unknown>>

function flatten(tree: Record<string, unknown>, prefix = ''): Map<string, unknown> {
  const result = new Map<string, unknown>()
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [inner, innerValue] of flatten(value as Record<string, unknown>, path)) result.set(inner, innerValue)
    } else {
      result.set(path, value)
    }
  }
  return result
}

describe('translations', () => {
  const names = Object.keys(files).sort()
  const languages = names.map((name) => flatten(files[name]))
  const reference = languages[0]

  it('exist for every language in the list, and only for those', () => {
    const codes = names.map((name) => name.replace(/^\.\//, '').replace(/\.json$/, '')).sort()
    expect(codes).toEqual(Object.keys(LANGUAGES).sort())
    expect(codes.length).toBeGreaterThanOrEqual(2)
  })

  it('have the same keys in every language', () => {
    for (const [index, language] of languages.entries()) {
      expect([...reference.keys()].filter((key) => !language.has(key)), `fehlt in ${names[index]}`).toEqual([])
      expect([...language.keys()].filter((key) => !reference.has(key)), `zu viel in ${names[index]}`).toEqual([])
    }
    // Floor check: an empty or truncated file should not silently pass.
    expect(reference.size).toBeGreaterThan(400)
  })

  it('have no empty texts', () => {
    const empty = languages.flatMap((language) => [...language]).filter(([, value]) => typeof value !== 'string' || value.trim() === '')
    expect(empty).toEqual([])
  })

  it('use no dashes as punctuation', () => {
    // House rule: no dashes in anything nextrmnl shows.
    const dashed = languages.flatMap((language) => [...language]).filter(([, value]) => /\s[–—]\s|—/.test(String(value)))
    expect(dashed).toEqual([])
  })

  it('keep the same placeholders in every language', () => {
    const placeholders = (text: unknown) => [...String(text).matchAll(/\{\{(\w+)\}\}/g)].map((match) => match[1]).sort()
    for (const key of reference.keys()) {
      const expected = placeholders(reference.get(key))
      for (const [index, language] of languages.entries()) {
        expect(placeholders(language.get(key)), `${key} in ${names[index]}`).toEqual(expected)
      }
    }
  })
})

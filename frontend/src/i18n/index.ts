/**
 * The languages, and only the one currently needed. The other one is only loaded when switching.
 * That is why there is no fallback language: if a text is missing, its key shows up, and
 * `complete.test.ts` makes sure all files have the same entries.
 *
 * A new language: put `xx.json` next to the others and add an entry to `LANGUAGES` here.
 * The switcher, browser detection and tests pick it up automatically.
 */

import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

type Texts = Record<string, unknown>

interface LanguageEntry {
  /** Short, for the switcher. */
  label: string
  /** The name in the language itself, for screen readers and the tooltip. */
  name: string
  load: () => Promise<{ default: Texts }>
}

export const LANGUAGES = {
  de: { label: 'DE', name: 'Deutsch', load: () => import('./de.json') },
  en: { label: 'EN', name: 'English', load: () => import('./en.json') },
} as const satisfies Record<string, LanguageEntry>

export type Language = keyof typeof LANGUAGES
export const SUPPORTED_LANGUAGES = Object.keys(LANGUAGES) as Language[]
/** If the browser speaks none of the languages. */
const FALLBACK: Language = 'en'

const STORAGE_KEY = 'nextrmnl.language'

export function isLanguage(value: unknown): value is Language {
  return typeof value === 'string' && value in LANGUAGES
}

/** The stored choice, otherwise the first browser language we have (only the language part counts: `de-AT` is `de`). */
function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (isLanguage(stored)) return stored
  } catch {
    // Private mode without localStorage.
  }
  for (const wanted of navigator.languages ?? [navigator.language]) {
    const primary = wanted.toLowerCase().split(/[-_]/)[0]
    const match = SUPPORTED_LANGUAGES.find((language) => language.toLowerCase().split(/[-_]/)[0] === primary)
    if (match) return match
  }
  return FALLBACK
}

async function load(language: Language): Promise<void> {
  if (i18n.hasResourceBundle(language, 'translation')) return
  const { default: texts } = await LANGUAGES[language].load()
  i18n.addResourceBundle(language, 'translation', texts)
}

export async function startI18n(language: Language = initialLanguage()): Promise<void> {
  const { default: texts } = await LANGUAGES[language].load()
  await i18n.use(initReactI18next).init({
    resources: { [language]: { translation: texts } },
    lng: language,
    fallbackLng: false,
    interpolation: { escapeValue: false },
  })
  document.documentElement.lang = language
}

export async function changeLanguage(language: Language): Promise<void> {
  try {
    localStorage.setItem(STORAGE_KEY, language)
  } catch {
    // Then the choice only holds until the next reload.
  }
  if (i18n.language === language) return
  await load(language)
  document.documentElement.lang = language
  await i18n.changeLanguage(language)
}

export default i18n

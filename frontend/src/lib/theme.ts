/**
 * Light or dark mode. The colors behind it live solely in
 * styles/index.css, this only holds which mode is active.
 */

export type Theme = 'dark' | 'light'

const KEY = 'nextrmnl.theme'

/** Terminals listen for this and fetch their colors again. */
export const THEME_EVENT = 'nextrmnl-theme'

export function storedTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'light') root.setAttribute('data-theme', 'light')
  else root.removeAttribute('data-theme')
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#f5f5f8' : '#0b0b0f')
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    // Then the choice only holds until the next reload.
  }
  window.dispatchEvent(new Event(THEME_EVENT))
}

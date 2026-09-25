import type { ITheme } from '@xterm/xterm'

import { storedTheme } from './theme'

function cssColor(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/** The ANSI colors are matched to the nex gray tones; background and text come from the CSS. */
export function terminalTheme(): ITheme {
  const light = storedTheme() === 'light'
  const base: ITheme = {
    background: cssColor('--color-term-bg', light ? '#fbfbfd' : '#0d0d13'),
    foreground: cssColor('--color-term-fg', light ? '#26262f' : '#dcdce4'),
    cursor: cssColor('--color-accent-500', light ? '#7c3aed' : '#a78bfa'),
    cursorAccent: cssColor('--color-term-bg', light ? '#fbfbfd' : '#0d0d13'),
    selectionBackground: light ? 'rgba(124, 58, 237, 0.22)' : 'rgba(167, 139, 250, 0.32)',
  }
  if (light) {
    return {
      ...base,
      black: '#26262f',
      red: '#be123c',
      green: '#15803d',
      yellow: '#a16207',
      blue: '#1d4ed8',
      magenta: '#7c3aed',
      cyan: '#0e7490',
      white: '#61616f',
      brightBlack: '#8a8a97',
      brightRed: '#e11d48',
      brightGreen: '#16a34a',
      brightYellow: '#ca8a04',
      brightBlue: '#2563eb',
      brightMagenta: '#8b5cf6',
      brightCyan: '#0891b2',
      brightWhite: '#14141a',
    }
  }
  return {
    ...base,
    black: '#26262f',
    red: '#fb7185',
    green: '#4ade80',
    yellow: '#fbbf24',
    blue: '#60a5fa',
    magenta: '#c4b5fd',
    cyan: '#67e8f9',
    white: '#c3c3ce',
    brightBlack: '#6f6f80',
    brightRed: '#fda4af',
    brightGreen: '#86efac',
    brightYellow: '#fde68a',
    brightBlue: '#93c5fd',
    brightMagenta: '#ddd6fe',
    brightCyan: '#a5f3fc',
    brightWhite: '#f2f2f5',
  }
}

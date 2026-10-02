import type { ITheme } from '@xterm/xterm'

/**
 * Color schemes for the terminal. `nex` follows the light or dark mode of the app; every other scheme keeps its
 * own colors in both modes, the way people know them from their desktop terminals.
 */
export const SCHEMES = ['nex', 'dracula', 'nord', 'solarizedDark', 'solarizedLight', 'gruvboxDark', 'oneDark', 'tokyoNight'] as const
export type SchemeId = (typeof SCHEMES)[number]

export function isScheme(value: unknown): value is SchemeId {
  return typeof value === 'string' && (SCHEMES as readonly string[]).includes(value)
}

/** Display names; these are proper names and stay the same in every language. */
export const SCHEME_NAMES: Record<Exclude<SchemeId, 'nex'>, string> = {
  dracula: 'Dracula',
  nord: 'Nord',
  solarizedDark: 'Solarized Dark',
  solarizedLight: 'Solarized Light',
  gruvboxDark: 'Gruvbox Dark',
  oneDark: 'One Dark',
  tokyoNight: 'Tokyo Night',
}

type Palette = [string, string, string, string, string, string, string, string, string, string, string, string, string, string, string, string]

function scheme(background: string, foreground: string, cursor: string, selection: string, palette: Palette): ITheme {
  const [black, red, green, yellow, blue, magenta, cyan, white, brightBlack, brightRed, brightGreen, brightYellow, brightBlue, brightMagenta, brightCyan, brightWhite] = palette
  return {
    background,
    foreground,
    cursor,
    cursorAccent: background,
    selectionBackground: selection,
    black,
    red,
    green,
    yellow,
    blue,
    magenta,
    cyan,
    white,
    brightBlack,
    brightRed,
    brightGreen,
    brightYellow,
    brightBlue,
    brightMagenta,
    brightCyan,
    brightWhite,
  }
}

export const FIXED_SCHEMES: Record<Exclude<SchemeId, 'nex'>, ITheme> = {
  dracula: scheme('#282a36', '#f8f8f2', '#f8f8f2', 'rgba(68, 71, 90, 0.9)', [
    '#21222c', '#ff5555', '#50fa7b', '#f1fa8c', '#bd93f9', '#ff79c6', '#8be9fd', '#f8f8f2',
    '#6272a4', '#ff6e6e', '#69ff94', '#ffffa5', '#d6acff', '#ff92df', '#a4ffff', '#ffffff',
  ]),
  nord: scheme('#2e3440', '#d8dee9', '#d8dee9', 'rgba(67, 76, 94, 0.9)', [
    '#3b4252', '#bf616a', '#a3be8c', '#ebcb8b', '#81a1c1', '#b48ead', '#88c0d0', '#e5e9f0',
    '#4c566a', '#bf616a', '#a3be8c', '#ebcb8b', '#81a1c1', '#b48ead', '#8fbcbb', '#eceff4',
  ]),
  solarizedDark: scheme('#002b36', '#839496', '#93a1a1', 'rgba(7, 54, 66, 0.95)', [
    '#073642', '#dc322f', '#859900', '#b58900', '#268bd2', '#d33682', '#2aa198', '#eee8d5',
    '#586e75', '#cb4b16', '#93a1a1', '#839496', '#657b83', '#6c71c4', '#93a1a1', '#fdf6e3',
  ]),
  solarizedLight: scheme('#fdf6e3', '#657b83', '#586e75', 'rgba(238, 232, 213, 0.95)', [
    '#073642', '#dc322f', '#859900', '#b58900', '#268bd2', '#d33682', '#2aa198', '#eee8d5',
    '#002b36', '#cb4b16', '#586e75', '#657b83', '#839496', '#6c71c4', '#93a1a1', '#fdf6e3',
  ]),
  gruvboxDark: scheme('#282828', '#ebdbb2', '#ebdbb2', 'rgba(80, 73, 69, 0.9)', [
    '#282828', '#cc241d', '#98971a', '#d79921', '#458588', '#b16286', '#689d6a', '#a89984',
    '#928374', '#fb4934', '#b8bb26', '#fabd2f', '#83a598', '#d3869b', '#8ec07c', '#ebdbb2',
  ]),
  oneDark: scheme('#282c34', '#abb2bf', '#528bff', 'rgba(62, 68, 81, 0.9)', [
    '#282c34', '#e06c75', '#98c379', '#e5c07b', '#61afef', '#c678dd', '#56b6c2', '#abb2bf',
    '#5c6370', '#e06c75', '#98c379', '#e5c07b', '#61afef', '#c678dd', '#56b6c2', '#ffffff',
  ]),
  tokyoNight: scheme('#1a1b26', '#c0caf5', '#c0caf5', 'rgba(40, 52, 87, 0.9)', [
    '#15161e', '#f7768e', '#9ece6a', '#e0af68', '#7aa2f7', '#bb9af7', '#7dcfff', '#a9b1d6',
    '#414868', '#f7768e', '#9ece6a', '#e0af68', '#7aa2f7', '#bb9af7', '#7dcfff', '#c0caf5',
  ]),
}

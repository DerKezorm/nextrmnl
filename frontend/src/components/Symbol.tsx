/**
 * The symbols in one place, like in Nexview, nexcrate and nexpulse: 24x24,
 * stroke instead of fill, `currentColor`.
 */

type Path = { d: string; fill?: boolean }

const SYMBOLS = {
  terminal: [{ d: 'M4 5.5h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1Z' }, { d: 'M7 10l3 2.5L7 15M12.5 15H17' }],
  sessions: [{ d: 'M3.5 20h17' }, { d: 'M6 16V9M10 16V5M14 16v-4M18 16V8' }],
  vault: [{ d: 'M5 10.5h14a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-7.5a1 1 0 0 1 1-1Z' }, { d: 'M8 10.5V8a4 4 0 1 1 8 0v2.5' }, { d: 'M12 14.5v2' }],
  unlocked: [{ d: 'M5 10.5h14a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-7.5a1 1 0 0 1 1-1Z' }, { d: 'M8 10.5V8a4 4 0 0 1 7.7-1.5' }, { d: 'M12 14.5v2' }],
  // Slider like in nexcrate. The gear icon looked like a sun and got confused with the light-mode switcher.
  settings: [{ d: 'M4 7h9M17 7h3M4 17h3M11 17h9' }, { d: 'M15 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM9 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z' }],
  server: [{ d: 'M4.5 4.5h15v6h-15zM4.5 13.5h15v6h-15z' }, { d: 'M8 7.5h.01M8 16.5h.01' }],
  folder: [{ d: 'M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5V7Z' }],
  file: [{ d: 'M6.5 3.5h7l4 4v13h-11z' }, { d: 'M13.5 3.5v4h4' }],
  files: [{ d: 'M8.5 7.5h11v12h-11z' }, { d: 'M15.5 7.5v-3h-11v12h4' }],
  key: [{ d: 'M8 15a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z' }, { d: 'M12 11h8.5M17.5 11v3M20.5 11v2' }],
  password: [{ d: 'M3.5 8.5h17v7h-17z' }, { d: 'M7.5 12h.01M11 12h.01M14.5 12h.01' }],
  users: [{ d: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z' }, { d: 'M2.5 20a6.5 6.5 0 0 1 13 0' }, { d: 'M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14a6.5 6.5 0 0 1 3.5 6' }],
  share: [{ d: 'M17 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM7 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM17 21a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z' }, { d: 'M9.2 10.8l5.6-3.1M9.2 13.2l5.6 3.1' }],
  jump: [{ d: 'M4 17.5c3-8 13-8 16 0' }, { d: 'M4 17.5h.01M20 17.5h.01M12 11.5v.01' }],
  shield: [{ d: 'M12 3.5 19.5 6v5.5c0 4.5-3.2 8-7.5 9-4.3-1-7.5-4.5-7.5-9V6L12 3.5Z' }, { d: 'M9 12l2 2 4-4' }],
  shieldAlert: [{ d: 'M12 3.5 19.5 6v5.5c0 4.5-3.2 8-7.5 9-4.3-1-7.5-4.5-7.5-9V6L12 3.5Z' }, { d: 'M12 8.5v4M12 15.5v.2' }],
  fingerprint: [{ d: 'M7 17.5c1-1.8 1.5-3.6 1.5-5.5a3.5 3.5 0 1 1 7 0c0 2.7-.6 5.2-1.8 7.5' }, { d: 'M4.5 14c.3-.7.5-1.3.5-2a7 7 0 0 1 12.5-4.3M19 11c.1 3-.4 5.6-1.5 8M12 12c0 3-.8 5.6-2.3 7.8' }],
  search: [{ d: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z' }, { d: 'M20 20l-4-4' }],
  chevronLeft: [{ d: 'M14.5 6 8.5 12l6 6' }],
  chevronRight: [{ d: 'M9.5 6l6 6-6 6' }],
  chevronDown: [{ d: 'M6 9.5l6 6 6-6' }],
  more: [{ d: 'M6 12h.01M12 12h.01M18 12h.01' }],
  upload: [{ d: 'M12 16V4M6.5 9.5 12 4l5.5 5.5' }, { d: 'M4 16.5V20h16v-3.5' }],
  download: [{ d: 'M12 4v12M6.5 10.5 12 16l5.5-5.5' }, { d: 'M4 16.5V20h16v-3.5' }],
  folderPlus: [{ d: 'M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5V7Z' }, { d: 'M12 10.5v5M9.5 13h5' }],
  pencil: [{ d: 'M15.5 5.5l3 3L8 19H5v-3L15.5 5.5Z' }],
  plug: [{ d: 'M9 3.5v4M15 3.5v4M6.5 7.5h11v3a5.5 5.5 0 0 1-11 0v-3Z' }, { d: 'M12 16v4.5' }],
  refresh: [{ d: 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4' }],
  expand: [{ d: 'M4.5 9.5v-5h5M19.5 9.5v-5h-5M4.5 14.5v5h5M19.5 14.5v5h-5' }],
  logout: [{ d: 'M14 4.5h5.5v15H14' }, { d: 'M10 8l-4 4 4 4M6 12h9' }],
  close: [{ d: 'M6 6l12 12M18 6 6 18' }],
  check: [{ d: 'M5 12.5l4.5 4.5L19 7.5' }],
  info: [{ d: 'M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z' }, { d: 'M12 11v5.5M12 7.8v.2' }],
  warn: [{ d: 'M12 4 2.8 19.5h18.4L12 4Z' }, { d: 'M12 10v4.5M12 17v.2' }],
  plus: [{ d: 'M12 5v14M5 12h14' }],
  trash: [{ d: 'M5 7h14' }, { d: 'M9.5 7V5h5v2' }, { d: 'M6.5 7l.8 12.1a1 1 0 0 0 1 .9h7.4a1 1 0 0 0 1-.9L17.5 7' }],
  copy: [{ d: 'M8.5 8.5h11v11h-11z' }, { d: 'M15.5 8.5V4.5h-11v11h4' }],
  up: [{ d: 'M12 19V5M5.5 11.5 12 5l6.5 6.5' }],
  menu: [{ d: 'M4 7h16M4 12h16M4 17h16' }],
  list: [{ d: 'M9 6.5h11M9 12h11M9 17.5h11' }, { d: 'M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01' }],
  pin: [{ d: 'M9 4h6l-1 5.5 3 3H7l3-3L9 4Z' }, { d: 'M12 12.5V20' }],
  shrink: [{ d: 'M9.5 4.5v5h-5M14.5 4.5v5h5M9.5 19.5v-5h-5M14.5 19.5v-5h5' }],
  external: [{ d: 'M14 4.5h5.5V10M19.5 4.5 10.5 13.5' }, { d: 'M16.5 13.5v5a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1h5' }],
} satisfies Record<string, Path[]>

export type SymbolName = keyof typeof SYMBOLS

export function Symbol({ name, className = 'h-4 w-4' }: { name: SymbolName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" focusable="false">
      {(SYMBOLS[name] as Path[]).map((path, index) => (
        <path
          key={index}
          d={path.d}
          fill={path.fill ? 'currentColor' : 'none'}
          stroke={path.fill ? 'none' : 'currentColor'}
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </svg>
  )
}

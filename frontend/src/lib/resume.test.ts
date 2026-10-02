import type { RunningSession } from '../api/types'
import { quickFromLabel, shellsToResume, storedTabs, storeTabs } from './resume'
import { cleanFontName, fontStack } from './terminalPrefs'
import { FIXED_SCHEMES, isScheme, SCHEME_NAMES, SCHEMES } from './terminalSchemes'

function shell(id: string, extra: Partial<RunningSession> = {}): RunningSession {
  return {
    id,
    account: 'admin',
    mine: true,
    detached: true,
    connection_id: 1,
    name: id,
    target: 'root@192.0.2.10:22',
    started_at: `2026-10-02T10:00:0${id.length}Z`,
    from_ip: '192.0.2.1',
    state: 'open',
    ...extra,
  }
}

describe('which shells come back after a reload', () => {
  it('keeps the order of this tab, then adds waiting ones, oldest first', () => {
    const running = [shell('ccc', { started_at: '2026-10-02T10:00:03Z' }), shell('a', { started_at: '2026-10-02T10:00:01Z' }), shell('bb', { detached: false })]
    expect(shellsToResume(running, ['bb', 'gone']).map((r) => r.id)).toEqual(['bb', 'a', 'ccc'])
  })

  it('leaves out foreign shells, shells still connecting, and shells another window shows', () => {
    const running = [shell('theirs', { mine: false }), shell('young', { state: 'connecting' }), shell('watched', { detached: false })]
    expect(shellsToResume(running, [])).toEqual([])
    expect(shellsToResume(running, ['theirs', 'young'])).toEqual([])
  })

  it('remembers the tabs per browser tab and survives broken storage', () => {
    storeTabs(['x', 'y'])
    expect(storedTabs()).toEqual(['x', 'y'])
    sessionStorage.setItem('nextrmnl.tabs', '{not json')
    expect(storedTabs()).toEqual([])
    sessionStorage.setItem('nextrmnl.tabs', '[1, "z", null]')
    expect(storedTabs()).toEqual(['z'])
  })
})

describe('quick connections from the server label', () => {
  it('reads user, host and port, IPv6 included', () => {
    expect(quickFromLabel('root@192.0.2.10:2222')).toEqual({ user: 'root', host: '192.0.2.10', port: 2222 })
    expect(quickFromLabel('admin@2001:db8::1:22')).toEqual({ user: 'admin', host: '2001:db8::1', port: 22 })
    expect(quickFromLabel('no label')).toBeNull()
  })
})

describe('terminal fonts', () => {
  it('keeps a font name and drops what could end the CSS value', () => {
    expect(cleanFontName('  FiraCode   Nerd Font Mono ')).toBe('FiraCode Nerd Font Mono')
    expect(cleanFontName('Evil"; background: url(x)')).toBe('Evil background urlx')
    expect(cleanFontName('x'.repeat(100))).toHaveLength(64)
  })

  it('puts the chosen font in front of the built-in ones', () => {
    expect(fontStack('', 'monospace')).toBe('monospace')
    expect(fontStack('Hack', 'monospace')).toBe('"Hack", monospace')
    expect(fontStack('";}', 'monospace')).toBe('monospace')
  })
})

describe('color schemes', () => {
  it('has a name and all sixteen colors for every fixed scheme', () => {
    const colors = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'] as const
    for (const id of SCHEMES) {
      expect(isScheme(id)).toBe(true)
      if (id === 'nex') continue
      expect(SCHEME_NAMES[id]).toBeTruthy()
      const theme = FIXED_SCHEMES[id]
      expect(theme.background).toMatch(/^#[0-9a-f]{6}$/)
      expect(theme.foreground).toMatch(/^#[0-9a-f]{6}$/)
      for (const color of colors) {
        expect(theme[color]).toMatch(/^#[0-9a-f]{6}$/)
        const bright = `bright${color[0].toUpperCase()}${color.slice(1)}` as keyof typeof theme
        expect(theme[bright]).toMatch(/^#[0-9a-f]{6}$/)
      }
    }
    expect(isScheme('dracula')).toBe(true)
    expect(isScheme('unknown')).toBe(false)
  })
})

/**
 * Reading connections from other tools: an OpenSSH client configuration (`~/.ssh/config`) and a PuTTY sessions
 * export (`.reg`). Only addresses, users, ports and jump hosts come over; keys and passwords never do, they live
 * in files and stores nextrmnl has no business reading.
 */

export interface ImportedConnection {
  name: string
  host: string
  port: number
  user: string
  /** The name of the jump host among the imported entries, or null. */
  jump: string | null
  /** Things the source had that nextrmnl does not take over, as short English codes for the dialog. */
  notes: string[]
}

export type ImportFormat = 'ssh-config' | 'putty'

export interface ImportResult {
  format: ImportFormat
  connections: ImportedConnection[]
  /** Entries left out, with the reason as a code: wildcards, Match blocks, other protocols. */
  skipped: { name: string; reason: string }[]
}

const DEFAULT_USER = 'root'
const MAX_ENTRIES = 1000

/** A .reg export from Windows is UTF-16 with a byte order mark; everything else is read as UTF-8. */
export function decodeImportFile(bytes: Uint8Array): string {
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(bytes.subarray(2))
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(bytes.subarray(2))
  return new TextDecoder('utf-8').decode(bytes)
}

export function detectFormat(text: string): ImportFormat {
  return /SimonTatham\\PuTTY\\Sessions\\/i.test(text) ? 'putty' : 'ssh-config'
}

export function parseImport(text: string): ImportResult {
  return detectFormat(text) === 'putty' ? parsePutty(text) : parseSshConfig(text)
}

function validHost(host: string): boolean {
  return host.length > 0 && host.length <= 255 && !/[\s/]/.test(host)
}

function validPort(port: number): boolean {
  return Number.isInteger(port) && port >= 1 && port <= 65535
}

function clip(value: string, length: number): string {
  return value.trim().slice(0, length)
}

/** `[user@]host[:port]`, IPv6 in brackets. */
function parseHop(hop: string): { user: string | null; host: string; port: number | null } {
  let rest = hop.trim()
  let user: string | null = null
  const at = rest.lastIndexOf('@')
  if (at > 0) {
    user = rest.slice(0, at)
    rest = rest.slice(at + 1)
  }
  let port: number | null = null
  const bracket = /^\[([^\]]+)\](?::(\d+))?$/.exec(rest)
  if (bracket) return { user, host: bracket[1], port: bracket[2] ? Number(bracket[2]) : null }
  const colon = rest.lastIndexOf(':')
  if (colon > 0 && rest.indexOf(':') === colon) {
    port = Number(rest.slice(colon + 1))
    rest = rest.slice(0, colon)
  }
  return { user, host: rest, port }
}

function unquote(value: string): string {
  const trimmed = value.trim()
  return trimmed.length >= 2 && trimmed.startsWith('"') && trimmed.endsWith('"') ? trimmed.slice(1, -1) : trimmed
}

type Block = { patterns: string[]; values: Map<string, string>; match: boolean }

/**
 * OpenSSH: `Host` blocks with HostName, User, Port, ProxyJump. As in ssh, the first value found wins: a host's
 * own block first, then what stands before every block and in `Host *`. Patterns with wildcards are not
 * connections and are skipped; `Match` blocks and `Include` are too.
 */
export function parseSshConfig(text: string): ImportResult {
  const blocks: Block[] = []
  const global: Block = { patterns: [], values: new Map(), match: false }
  let current = global
  const skipped: ImportResult['skipped'] = []
  let includes = 0

  for (const rawLine of text.split(/\r\n|\r|\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const match = /^(\S+?)\s*(?:=\s*|\s+)(.*)$/.exec(line)
    if (!match) continue
    const keyword = match[1].toLowerCase()
    const value = unquote(match[2])
    if (keyword === 'host') {
      current = { patterns: value.split(/\s+/).filter(Boolean), values: new Map(), match: false }
      blocks.push(current)
      continue
    }
    if (keyword === 'match') {
      current = { patterns: [], values: new Map(), match: true }
      skipped.push({ name: `Match ${value}`.slice(0, 64), reason: 'match' })
      continue
    }
    if (keyword === 'include') {
      includes += 1
      continue
    }
    if (!current.values.has(keyword)) current.values.set(keyword, value)
  }
  if (includes > 0) skipped.push({ name: 'Include', reason: 'include' })

  const defaults = [global, ...blocks.filter((block) => block.patterns.includes('*'))]
  const lookup = (block: Block, keyword: string): string | undefined => {
    if (block.values.has(keyword)) return block.values.get(keyword)
    for (const fallback of defaults) if (fallback.values.has(keyword)) return fallback.values.get(keyword)
    return undefined
  }

  const connections: ImportedConnection[] = []
  const extraJumps: ImportedConnection[] = []
  for (const block of blocks) {
    if (block.match) continue
    const names = block.patterns.filter((pattern) => !/[*?!]/.test(pattern))
    if (names.length === 0) {
      if (!block.patterns.includes('*')) skipped.push({ name: block.patterns.join(' ').slice(0, 64), reason: 'pattern' })
      continue
    }
    const name = clip(names[0], 64)
    const host = clip(lookup(block, 'hostname') ?? names[0], 255).replace(/%h/g, names[0])
    const port = Number(lookup(block, 'port') ?? 22)
    if (!validHost(host) || !validPort(port)) {
      skipped.push({ name, reason: 'invalid' })
      continue
    }
    const notes: string[] = []
    if (lookup(block, 'identityfile')) notes.push('key_file')
    if (lookup(block, 'proxycommand')) notes.push('proxy_command')
    let jump: string | null = null
    const proxyJump = lookup(block, 'proxyjump')
    if (proxyJump && proxyJump.toLowerCase() !== 'none') {
      const hops = proxyJump.split(',').map((hop) => hop.trim()).filter(Boolean)
      if (hops.length > 1) notes.push('jump_chain')
      // The hop right before the target; nextrmnl follows the jump host's own jump for longer chains.
      const hop = parseHop(hops[hops.length - 1])
      if (blocks.some((other) => other.patterns.includes(hop.host)) && hop.user === null && hop.port === null) {
        jump = clip(hop.host, 64)
      } else if (validHost(hop.host)) {
        const jumpPort = hop.port ?? 22
        const existing = extraJumps.find((entry) => entry.host === hop.host && entry.port === jumpPort && entry.user === (hop.user ?? DEFAULT_USER))
        if (existing) jump = existing.name
        else if (validPort(jumpPort)) {
          const jumpName = clip(`${hop.host}${jumpPort !== 22 ? `:${jumpPort}` : ''} (jump)`, 64)
          extraJumps.push({ name: jumpName, host: hop.host, port: jumpPort, user: clip(hop.user ?? DEFAULT_USER, 64), jump: null, notes: [] })
          jump = jumpName
        }
      }
    }
    connections.push({ name, host, port, user: clip(lookup(block, 'user') ?? DEFAULT_USER, 64) || DEFAULT_USER, jump, notes })
    if (connections.length >= MAX_ENTRIES) break
  }
  return { format: 'ssh-config', connections: [...extraJumps, ...connections].slice(0, MAX_ENTRIES), skipped }
}

/** A string value of a .reg file: backslash escapes for backslash and quote. */
function regString(value: string): string {
  return value.replace(/\\(["\\])/g, '$1')
}

/**
 * PuTTY: `reg export HKCU\Software\SimonTatham\PuTTY\Sessions`. Each session is a key with HostName, PortNumber
 * (hex dword), UserName and Protocol. Session names are percent-encoded. Only SSH sessions come over.
 */
export function parsePutty(text: string): ImportResult {
  const connections: ImportedConnection[] = []
  const skipped: ImportResult['skipped'] = []
  const sections: { name: string; values: Map<string, string> }[] = []
  let current: { name: string; values: Map<string, string> } | null = null

  for (const rawLine of text.split(/\r\n|\r|\n/)) {
    const line = rawLine.trim()
    const section = /^\[HKEY_[^\]]*\\SimonTatham\\PuTTY\\Sessions\\([^\]\\]+)\]$/i.exec(line)
    if (section) {
      let name = section[1]
      try {
        name = decodeURIComponent(name)
      } catch {
        // A stray percent sign; the raw name will do.
      }
      current = { name, values: new Map() }
      sections.push(current)
      continue
    }
    if (line.startsWith('[')) {
      current = null
      continue
    }
    if (!current) continue
    const value = /^"([^"]+)"=(?:"((?:[^"\\]|\\.)*)"|dword:([0-9a-fA-F]{8}))$/.exec(line)
    if (!value) continue
    current.values.set(value[1].toLowerCase(), value[2] !== undefined ? regString(value[2]) : String(parseInt(value[3], 16)))
  }

  for (const { name: rawName, values } of sections) {
    const name = clip(rawName, 64)
    if (name === 'Default Settings') continue
    const protocol = (values.get('protocol') ?? 'ssh').toLowerCase()
    if (protocol !== 'ssh') {
      skipped.push({ name, reason: 'protocol' })
      continue
    }
    const target = parseHop(values.get('hostname') ?? '')
    const host = clip(target.host, 255)
    const port = Number(values.get('portnumber') ?? target.port ?? 22)
    if (!validHost(host) || !validPort(port)) {
      skipped.push({ name, reason: host ? 'invalid' : 'no_host' })
      continue
    }
    const notes: string[] = []
    if (values.get('publickeyfile')) notes.push('key_file')
    if ((values.get('proxymethod') ?? '0') !== '0') notes.push('proxy_command')
    const user = clip(values.get('username') || target.user || DEFAULT_USER, 64)
    connections.push({ name, host, port, user, jump: null, notes })
    if (connections.length >= MAX_ENTRIES) break
  }
  return { format: 'putty', connections, skipped }
}

/** The order to create them in: a jump host before whoever jumps through it. Entries in a loop come last, alone. */
export function creationOrder(entries: ImportedConnection[]): ImportedConnection[] {
  const byName = new Map(entries.map((entry) => [entry.name, entry]))
  const done = new Set<string>()
  const result: ImportedConnection[] = []
  const visiting = new Set<string>()
  const visit = (entry: ImportedConnection) => {
    if (done.has(entry.name) || visiting.has(entry.name)) return
    visiting.add(entry.name)
    const jump = entry.jump ? byName.get(entry.jump) : undefined
    if (jump) visit(jump)
    visiting.delete(entry.name)
    done.add(entry.name)
    result.push(entry)
  }
  entries.forEach(visit)
  return result
}

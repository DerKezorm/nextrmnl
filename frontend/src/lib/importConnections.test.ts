import { creationOrder, decodeImportFile, detectFormat, parseImport, parsePutty, parseSshConfig } from './importConnections'

const SSH_CONFIG = `
# defaults for everyone
User admin

Host web-01
    HostName 192.0.2.10
    Port 2222
    IdentityFile ~/.ssh/id_ed25519

Host bastion
  HostName=bastion.example.com
  User jump

Host db internal-db
  HostName 192.0.2.20
  ProxyJump bastion
  User "postgres"

Host behind-raw
  HostName 10.0.0.9
  ProxyJump ops@198.51.100.4:2200

Host behind-chain
  HostName 10.0.0.10
  ProxyJump bastion,ops@198.51.100.4:2200

Host *.example.com
  User nobody

Match host foo
  User ignored

Include ~/.ssh/config.d/*

Host *
  Port 22
  ServerAliveInterval 30
`

describe('reading an OpenSSH config', () => {
  const result = parseSshConfig(SSH_CONFIG)
  const byName = Object.fromEntries(result.connections.map((c) => [c.name, c]))

  it('takes host, port and user, with defaults from before the first block and from Host *', () => {
    expect(byName['web-01']).toMatchObject({ host: '192.0.2.10', port: 2222, user: 'admin', jump: null, notes: ['key_file'] })
    expect(byName['bastion']).toMatchObject({ host: 'bastion.example.com', port: 22, user: 'jump' })
    expect(byName['db']).toMatchObject({ host: '192.0.2.20', user: 'postgres', jump: 'bastion' })
  })

  it('turns a jump host that is no block into its own entry, once', () => {
    expect(byName['behind-raw'].jump).toBe('198.51.100.4:2200 (jump)')
    expect(byName['198.51.100.4:2200 (jump)']).toMatchObject({ host: '198.51.100.4', port: 2200, user: 'ops' })
    expect(byName['behind-chain'].jump).toBe('198.51.100.4:2200 (jump)')
    expect(byName['behind-chain'].notes).toContain('jump_chain')
    expect(result.connections.filter((c) => c.name.endsWith('(jump)'))).toHaveLength(1)
  })

  it('leaves out patterns, Match blocks and includes, and says so', () => {
    expect(result.connections.map((c) => c.name)).not.toContain('*.example.com')
    expect(result.skipped.map((s) => s.reason).sort()).toEqual(['include', 'match', 'pattern'])
  })

  it('skips what is no address and falls back to root without any user', () => {
    const odd = parseSshConfig('Host bad\n HostName has space\nHost noport\n Port 99999\nHost plain\n')
    expect(odd.connections.map((c) => c.name)).toEqual(['plain'])
    expect(odd.connections[0]).toMatchObject({ host: 'plain', user: 'root', port: 22 })
    expect(odd.skipped.filter((s) => s.reason === 'invalid')).toHaveLength(2)
  })
})

const PUTTY = String.raw`Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions]

[HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions\Default%20Settings]
"HostName"=""

[HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions\NAS%20im%20Keller]
"HostName"="192.0.2.30"
"PortNumber"=dword:00000016
"UserName"="admin"
"Protocol"="ssh"
"PublicKeyFile"="C:\\Users\\someone\\nas.ppk"

[HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions\router]
"HostName"="root@192.0.2.1"
"PortNumber"=dword:00000d3d
"Protocol"="ssh"

[HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions\switch]
"HostName"="192.0.2.2"
"Protocol"="telnet"
`

describe('reading a PuTTY export', () => {
  it('takes SSH sessions with decoded names, hex ports and the user from the host field', () => {
    const result = parsePutty(PUTTY)
    expect(result.connections).toEqual([
      { name: 'NAS im Keller', host: '192.0.2.30', port: 22, user: 'admin', jump: null, notes: ['key_file'] },
      { name: 'router', host: '192.0.2.1', port: 3389, user: 'root', jump: null, notes: [] },
    ])
    expect(result.skipped).toEqual([{ name: 'switch', reason: 'protocol' }])
  })

  it('recognises the format and reads the UTF-16 file Windows writes', () => {
    expect(detectFormat(PUTTY)).toBe('putty')
    expect(detectFormat(SSH_CONFIG)).toBe('ssh-config')
    const utf16 = new Uint8Array([0xff, 0xfe, ...Array.from(PUTTY).flatMap((c) => [c.charCodeAt(0) & 0xff, c.charCodeAt(0) >> 8])])
    expect(parseImport(decodeImportFile(utf16)).connections).toHaveLength(2)
    expect(decodeImportFile(new TextEncoder().encode('Host a\n'))).toBe('Host a\n')
  })
})

describe('the order of creation', () => {
  it('puts a jump host before whoever needs it and survives a loop', () => {
    const entry = (name: string, jump: string | null) => ({ name, host: name, port: 22, user: 'root', jump, notes: [] })
    expect(creationOrder([entry('a', 'b'), entry('b', 'c'), entry('c', null)]).map((e) => e.name)).toEqual(['c', 'b', 'a'])
    expect(creationOrder([entry('x', 'y'), entry('y', 'x')]).map((e) => e.name).sort()).toEqual(['x', 'y'])
  })
})

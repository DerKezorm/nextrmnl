// A stand-in authentik for the end-to-end tests of sign-in (nexoidc.spec.ts), never anything real:
// - the part of the API v3 the authentik button uses (version, signing key, scope mappings, flows, OAuth2 providers,
//   applications), answering only the token it was started with
//   (FAKE_AK_TOKEN, made by playwright.config.ts);
// - the OpenID Connect provider of every application it made: discovery under /application/o/<slug>/, and the
//   browser's visit to /application/o/authorize answers at once with a code for whoever POST /next named; the token is
//   signed with a key made here at the start.
import crypto from 'node:crypto'
import http from 'node:http'

const PORT = Number(process.env.FAKE_AK_PORT ?? 8477)
const TOKEN = process.env.FAKE_AK_TOKEN ?? crypto.randomBytes(24).toString('hex')
const BASE = `http://127.0.0.1:${PORT}`
const KID = 'e2e-key'

const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 })
let next = { sub: 'person-1', email: 'person@example.com', preferred_username: 'person', name: 'Person' }
const codes = new Map()
const providers = []
const applications = []
const certificates = []
const users = []
let counter = 40
const pk = () => ++counter
const MANAGED = ['openid', 'email', 'profile'].map((scope) => `goauthentik.io/providers/oauth2/scope-${scope}`)

const b64url = (value) => Buffer.from(value).toString('base64url')

function idToken(claims) {
  const head = b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT', kid: KID }))
  const body = b64url(JSON.stringify(claims))
  const signature = crypto.sign('RSA-SHA256', Buffer.from(`${head}.${body}`), privateKey).toString('base64url')
  return `${head}.${body}.${signature}`
}

const list = (rows) => ({ pagination: { count: rows.length }, results: rows })

function providerView(provider) {
  const assigned = applications.find((app) => app.provider === provider.pk)
  return { ...provider, assigned_application_slug: assigned?.slug ?? '', assigned_application_name: assigned?.name ?? '' }
}

/** The API v3: [status, answer]. */
function api(method, path, query, body) {
  if (method === 'GET' && path === '/admin/version/') return [200, { version_current: '2026.8.1' }]
  if (method === 'GET' && path === '/crypto/certificatekeypairs/') {
    return [200, list(certificates.filter((row) => !query.get('name') || row.name === query.get('name')))]
  }
  if (method === 'POST' && path === '/crypto/certificatekeypairs/generate/') {
    const row = { pk: `cert-${pk()}`, name: body.common_name }
    certificates.push(row)
    return [200, row]
  }
  if (method === 'GET' && path === '/propertymappings/provider/scope/') {
    const rows = MANAGED.map((name) => ({ pk: `map-${name.split('-').pop()}`, managed: name, name }))
    return [200, list(rows.filter((row) => !query.get('managed') || row.managed === query.get('managed')))]
  }
  if (method === 'GET' && path === '/flows/instances/') {
    return query.get('designation') === 'authorization'
      ? [200, list([{ pk: 'flow-implicit', slug: 'default-provider-authorization-implicit-consent' }])]
      : [200, list([{ pk: 'flow-invalidation', slug: 'default-provider-invalidation-flow' }])]
  }
  if (method === 'GET' && path === '/providers/oauth2/') {
    let rows = providers.map(providerView)
    for (const key of ['name', 'client_id']) if (query.get(key)) rows = rows.filter((row) => row[key] === query.get(key))
    return [200, list(rows)]
  }
  if (method === 'POST' && path === '/providers/oauth2/') {
    const provider = { ...body, pk: pk(), client_id: `cid-${crypto.randomBytes(6).toString('hex')}`, client_secret: crypto.randomBytes(24).toString('hex') }
    providers.push(provider)
    return [201, providerView(provider)]
  }
  let found = path.match(/^\/providers\/oauth2\/(\d+)\/$/)
  if (found && method === 'PATCH') {
    const provider = providers.find((row) => row.pk === Number(found[1]))
    if (!provider) return [404, { detail: 'Not found.' }]
    Object.assign(provider, body)
    return [200, providerView(provider)]
  }
  if (method === 'GET' && path === '/core/applications/') {
    if (query.get('superuser_full_list') !== 'true') return [200, list([])]
    return [200, list(applications.filter((row) => !query.get('slug') || row.slug === query.get('slug')))]
  }
  if (method === 'POST' && path === '/core/applications/') {
    if (applications.some((row) => row.slug === body.slug)) return [400, { slug: ['Application with this slug already exists.'] }]
    const application = { pk: `app-${pk()}`, ...body }
    applications.push(application)
    return [201, application]
  }
  found = path.match(/^\/core\/applications\/([^/]+)\/$/)
  if (found && method === 'PATCH') {
    const application = applications.find((row) => row.slug === found[1])
    if (!application) return [404, { detail: 'Not found.' }]
    Object.assign(application, body)
    return [200, application]
  }
  if (method === 'GET' && path === '/core/users/me/') return [200, { user: { pk: 4, username: 'owner' } }]
  if (method === 'GET' && path === '/core/users/') {
    const search = (query.get('search') ?? '').toLowerCase()
    const rows = users.filter((user) =>
      ['username', 'email', 'uuid'].every((key) => !query.get(key) || String(user[key]).toLowerCase() === query.get(key).toLowerCase()) &&
      (!search || [user.username, user.name, user.email].some((value) => String(value).toLowerCase().includes(search))),
    )
    return [200, list(rows)]
  }
  if (method === 'POST' && path === '/core/users/') {
    const user = { ...body, pk: pk(), uuid: crypto.randomUUID() }
    users.push(user)
    return [201, user]
  }
  found = path.match(/^\/core\/users\/(\d+)\/(set_password\/)?$/)
  if (found) {
    const user = users.find((row) => row.pk === Number(found[1]))
    if (!user) return [404, { detail: 'Not found.' }]
    if (found[2]) return [204, null]
    if (method === 'PATCH') Object.assign(user, body)
    if (method === 'DELETE') users.splice(users.indexOf(user), 1)
    return [200, user]
  }
  return [404, { detail: `no stand-in answer for ${method} ${path}` }]
}

http
  .createServer((request, response) => {
    let raw = ''
    request.on('data', (chunk) => (raw += chunk))
    request.on('end', () => {
      const url = new URL(request.url, BASE)
      const send = (status, value) => {
        response.writeHead(status, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify(value))
      }
      if (url.pathname === '/health') return send(200, { ok: true })
      if (url.pathname === '/next' && request.method === 'POST') {
        next = JSON.parse(raw || '{}')
        return send(200, next)
      }
      if (url.pathname.startsWith('/api/v3/')) {
        if (request.headers.authorization !== `Bearer ${TOKEN}`) return send(403, { detail: 'Authentication credentials were not provided.' })
        const [status, answer] = api(request.method, url.pathname.slice('/api/v3'.length), url.searchParams, raw ? JSON.parse(raw) : null)
        if (status === 204) {
          response.writeHead(204)
          return response.end()
        }
        return send(status, answer)
      }
      const discovery = url.pathname.match(/^\/application\/o\/([^/]+)\/\.well-known\/openid-configuration$/)
      if (discovery) {
        const application = applications.find((row) => row.slug === discovery[1] && row.provider)
        if (!application) return send(404, { detail: 'not found' })
        return send(200, {
          issuer: `${BASE}/application/o/${application.slug}/`,
          authorization_endpoint: `${BASE}/application/o/authorize/`,
          token_endpoint: `${BASE}/application/o/token/`,
          jwks_uri: `${BASE}/application/o/${application.slug}/jwks/`,
          userinfo_endpoint: `${BASE}/application/o/userinfo/`,
        })
      }
      if (/^\/application\/o\/[^/]+\/jwks\/$/.test(url.pathname)) {
        return send(200, { keys: [{ ...publicKey.export({ format: 'jwk' }), kid: KID, use: 'sig', alg: 'RS256' }] })
      }
      if (url.pathname === '/application/o/authorize/') {
        // Whoever was named last signs in, at once: the browser goes straight back to nextrmnl with a code.
        const provider = providers.find((row) => row.client_id === url.searchParams.get('client_id'))
        const application = provider && applications.find((row) => row.provider === provider.pk)
        if (!application) return send(400, { error: 'invalid_client' })
        const code = crypto.randomBytes(12).toString('hex')
        codes.set(code, {
          claims: next,
          nonce: url.searchParams.get('nonce'),
          challenge: url.searchParams.get('code_challenge'),
          client: provider.client_id,
          issuer: `${BASE}/application/o/${application.slug}/`,
        })
        const back = new URL(url.searchParams.get('redirect_uri'))
        back.searchParams.set('code', code)
        back.searchParams.set('state', url.searchParams.get('state') ?? '')
        response.writeHead(302, { Location: back.toString() })
        return response.end()
      }
      if (url.pathname === '/application/o/token/' && request.method === 'POST') {
        const form = new URLSearchParams(raw)
        const kept = codes.get(form.get('code') ?? '')
        codes.delete(form.get('code') ?? '')
        const challenge = crypto.createHash('sha256').update(form.get('code_verifier') ?? '').digest('base64url')
        if (!kept || challenge !== kept.challenge) return send(400, { error: 'invalid_grant' })
        const now = Math.floor(Date.now() / 1000)
        const claims = { iss: kept.issuer, aud: kept.client, exp: now + 300, iat: now, nonce: kept.nonce, ...kept.claims }
        return send(200, { id_token: idToken(claims), access_token: `access-${kept.claims.sub}`, token_type: 'Bearer' })
      }
      if (url.pathname === '/application/o/userinfo/') return send(404, { error: 'not here' })
      send(404, { error: 'not found' })
    })
  })
  .listen(PORT, '127.0.0.1')

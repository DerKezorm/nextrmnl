import { oidcStartHref } from './oidcStart'

describe('oidcStartHref', () => {
  it('starts at the provider named by its short name', () => {
    expect(oidcStartHref('sso')).toBe('/api/oidc/sso/start')
    expect(oidcStartHref('a b')).toBe('/api/oidc/a%20b/start')
  })

  it('carries a direct link to the provider and back', () => {
    expect(oidcStartHref('sso', '/connect/12')).toBe('/api/oidc/sso/start?next=%2Fconnect%2F12')
  })

  it('takes nothing along from the start page, the sign-in page or an invitation', () => {
    expect(oidcStartHref('sso', '/')).toBe('/api/oidc/sso/start')
    expect(oidcStartHref('sso', '/login')).toBe('/api/oidc/sso/start')
    expect(oidcStartHref('sso', '')).toBe('/api/oidc/sso/start')
    expect(oidcStartHref('sso', '/invite/abc')).toBe('/api/oidc/sso/start')
  })

  it('takes an invitation along', () => {
    expect(oidcStartHref('sso', '', 'Ab_c-1')).toBe('/api/oidc/sso/start?invite=Ab_c-1')
  })

  it('encodes what it carries, the server decides what counts', () => {
    expect(oidcStartHref('sso', '/a&next=//example.com')).toBe('/api/oidc/sso/start?next=%2Fa%26next%3D%2F%2Fexample.com')
  })
})

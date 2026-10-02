import { describe, expect, it } from 'vitest'

import { oidcStartHref } from './oidcStart'

describe('oidcStartHref', () => {
  it('carries a direct link to the provider and back', () => {
    expect(oidcStartHref('/connect/12')).toBe('/api/oidc/start?next=%2Fconnect%2F12')
  })

  it('carries nothing from the start page or the sign-in page', () => {
    expect(oidcStartHref('/')).toBe('/api/oidc/start')
    expect(oidcStartHref('/login')).toBe('/api/oidc/start')
    expect(oidcStartHref('')).toBe('/api/oidc/start')
  })

  it('encodes what it carries, so nothing can add its own parameters', () => {
    expect(oidcStartHref('/a&next=//example.com')).toBe('/api/oidc/start?next=%2Fa%26next%3D%2F%2Fexample.com')
  })
})

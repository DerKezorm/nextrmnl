import { base64urlToBuffer, bufferToBase64url, creationOptions, requestOptions } from './webauthn'

describe('WebAuthn options between JSON and the browser', () => {
  it('turns base64url into bytes and back, without padding', () => {
    const bytes = new Uint8Array([0, 1, 250, 251, 252, 253, 254, 255])
    const text = bufferToBase64url(bytes.buffer)
    expect(text).not.toMatch(/[+/=]/)
    expect(new Uint8Array(base64urlToBuffer(text))).toEqual(bytes)
    expect(bufferToBase64url(new Uint8Array([104, 105]))).toBe('aGk')
  })

  it('makes buffers of challenge, user id and the key lists', () => {
    const creation = creationOptions({
      challenge: 'aGVsbG8',
      rp: { id: 'ssh.example.com', name: 'nextrmnl' },
      user: { id: 'bmV4dHJtbmw6MQ', name: 'admin', displayName: 'admin' },
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
      excludeCredentials: [{ id: 'AQID', type: 'public-key' }],
    })
    expect(new TextDecoder().decode(creation.challenge as ArrayBuffer)).toBe('hello')
    expect(new TextDecoder().decode(creation.user.id as ArrayBuffer)).toBe('nextrmnl:1')
    expect(new Uint8Array(creation.excludeCredentials?.[0].id as ArrayBuffer)).toEqual(new Uint8Array([1, 2, 3]))
    expect(creation.rp.id).toBe('ssh.example.com')

    const request = requestOptions({ challenge: 'aGVsbG8', rpId: 'ssh.example.com', allowCredentials: [{ id: 'AQID', type: 'public-key' }] })
    expect(new Uint8Array(request.allowCredentials?.[0].id as ArrayBuffer)).toEqual(new Uint8Array([1, 2, 3]))
    expect(requestOptions({ challenge: 'aGVsbG8' }).allowCredentials).toBeUndefined()
  })
})

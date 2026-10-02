/**
 * The browser side of passkeys. The server sends the WebAuthn options as JSON (base64url for every binary field)
 * and wants the answer back the same way; the browser API speaks ArrayBuffers. Newer browsers convert themselves
 * (`parseCreationOptionsFromJSON`, `toJSON`); for the others it happens here.
 */

export function base64urlToBuffer(text: string): ArrayBuffer {
  const base64 = text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4)
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return bytes.buffer
}

export function bufferToBase64url(buffer: ArrayBuffer | ArrayBufferView): string {
  const bytes = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Passkeys only exist on HTTPS or localhost, and only in browsers that have the API. */
export function passkeysAvailable(): boolean {
  return typeof window !== 'undefined' && window.isSecureContext && typeof window.PublicKeyCredential === 'function'
}

type Json = Record<string, unknown>
type Descriptor = { id: string; type: string; transports?: string[] }

function descriptors(list: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(list)) return undefined
  return (list as Descriptor[]).map((entry) => ({ ...entry, id: base64urlToBuffer(entry.id), type: 'public-key' }) as PublicKeyCredentialDescriptor)
}

export function creationOptions(json: Json): PublicKeyCredentialCreationOptions {
  const user = json.user as Json
  return {
    ...(json as unknown as PublicKeyCredentialCreationOptions),
    challenge: base64urlToBuffer(String(json.challenge)),
    user: { ...(user as unknown as PublicKeyCredentialUserEntity), id: base64urlToBuffer(String(user.id)) },
    excludeCredentials: descriptors(json.excludeCredentials),
  }
}

export function requestOptions(json: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(json as unknown as PublicKeyCredentialRequestOptions),
    challenge: base64urlToBuffer(String(json.challenge)),
    allowCredentials: descriptors(json.allowCredentials),
  }
}

/** The answer of `create` or `get` as JSON the server reads. */
export function credentialToJson(credential: PublicKeyCredential): Json {
  const response = credential.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse
  const out: Json = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    clientExtensionResults: credential.getClientExtensionResults?.() ?? {},
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
  }
  const body: Json = { clientDataJSON: bufferToBase64url(response.clientDataJSON) }
  if ('attestationObject' in response && response.attestationObject) {
    body.attestationObject = bufferToBase64url(response.attestationObject)
    body.transports = typeof response.getTransports === 'function' ? response.getTransports() : []
  }
  if ('authenticatorData' in response && response.authenticatorData) {
    body.authenticatorData = bufferToBase64url(response.authenticatorData)
    body.signature = bufferToBase64url(response.signature)
    body.userHandle = response.userHandle ? bufferToBase64url(response.userHandle) : undefined
  }
  out.response = body
  return out
}

/** Asks the browser for a new passkey; throws when the person cancels or no key answers. */
export async function createPasskey(optionsJson: string): Promise<Json> {
  const credential = (await navigator.credentials.create({ publicKey: creationOptions(JSON.parse(optionsJson) as Json) })) as PublicKeyCredential | null
  if (!credential) throw new Error('cancelled')
  return credentialToJson(credential)
}

/** Asks the browser to sign the sign-in challenge with one of the account's keys. */
export async function answerWithPasskey(optionsJson: string): Promise<Json> {
  const credential = (await navigator.credentials.get({ publicKey: requestOptions(JSON.parse(optionsJson) as Json) })) as PublicKeyCredential | null
  if (!credential) throw new Error('cancelled')
  return credentialToJson(credential)
}

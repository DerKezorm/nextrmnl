/**
 * nexoidc in the browser: the fixed codes and names the server sends, the keys of their texts, and the slug the
 * provider form suggests. No framework, no app parts: the cards, buttons and dialogs are the app's own (Bauplan 04),
 * the words come from `oidc.de.json` and `oidc.en.json` next to this file.
 *
 * Copied unchanged into every app by `bauplaene/oidc/tools/sync.py`; changed only in `bauplaene/oidc`.
 *
 * The wording files use i18next's `{{app}}` for the name of the app. Set it once, for example
 * `i18n.init({ interpolation: { defaultVariables: { app: 'nexlore' } } })`, and merge the files into the app's
 * resources under the same keys (`oidc.…`) through `oidcWording` (since 1.0.9; it picks the sentences of apps with
 * one account).
 */

/** The error codes of Bauplan 02; each has a sentence under `oidc.error.<code>`. Same order as `errors.CODES`. */
export const OIDC_ERROR_CODES = [
  'oidc_not_configured',
  'oidc_provider_unreachable',
  'oidc_provider_invalid',
  'oidc_issuer_mismatch',
  'oidc_state_mismatch',
  'oidc_provider_error',
  'oidc_token_refused',
  'oidc_token_invalid',
  'oidc_no_signing_key',
  'oidc_no_account',
  'oidc_subject_taken',
  'oidc_only_account',
  'oidc_link_mismatch',
  'invite_invalid',
  'account_blocked',
  'too_many_attempts',
] as const

/** Problems with the provider form, under `oidc.formError.<code>`. Same order as `errors.FORM_CODES`. */
export const OIDC_FORM_CODES = [
  'slug_invalid',
  'slug_taken',
  'label_required',
  'issuer_invalid',
  'client_id_required',
  'client_secret_invalid',
  'scopes_invalid',
  'provider_managed',
  'provider_unknown',
] as const

/** The steps of the authentik button in their order; `binding` only in apps with one account. */
export const AUTHENTIK_STEPS = ['reached', 'signingKey', 'mapping', 'provider', 'application', 'filled'] as const
export const AUTHENTIK_STEP_BINDING = 'binding'
/** Why a step failed, under `oidc.authentik.why.<reason>` (with `{{status}}`; `issuer_change` with `{{count}}`). */
export const AUTHENTIK_REASONS = [
  'unreachable',
  'unusable',
  'token',
  'malformed',
  'answered',
  'slug',
  'coupled',
  'issuer_change',
] as const

export type OidcErrorCode = (typeof OIDC_ERROR_CODES)[number]

/** The text key of an error code from `?error=`, or null for anything else (never show a raw code from the address). */
export function oidcErrorKey(code: string | null | undefined): string | null {
  return code && (OIDC_ERROR_CODES as readonly string[]).includes(code) ? `oidc.error.${code}` : null
}

/** The text key of a form problem, or null. */
export function oidcFormErrorKey(code: string | null | undefined): string | null {
  return code && (OIDC_FORM_CODES as readonly string[]).includes(code) ? `oidc.formError.${code}` : null
}

/** The text key of a step's name. */
export function authentikStepKey(step: string): string | null {
  const known = (AUTHENTIK_STEPS as readonly string[]).includes(step) || step === AUTHENTIK_STEP_BINDING
  return known ? `oidc.authentik.step.${step}` : null
}

/** The text key of a step's reason. */
export function authentikReasonKey(reason: string | null | undefined): string | null {
  return reason && (AUTHENTIK_REASONS as readonly string[]).includes(reason) ? `oidc.authentik.why.${reason}` : null
}

/**
 * The number of accounts the button asks about before it changes the issuer (`reason: issuer_change`, Bauplan 03), or
 * null when the run did not stop for that. The page then asks with `oidc.admin.issuerChangeConfirm` and, on yes, runs
 * the button again with `confirm_issuer_change: true`.
 */
export function authentikIssuerChange(result: {
  steps: ReadonlyArray<{ ok: boolean; reason?: string }>
  count?: number
}): number | null {
  const failed = result.steps.find((step) => !step.ok)
  return failed?.reason === 'issuer_change' ? (result.count ?? 0) : null
}

/** A wording file (`oidc.de.json`, `oidc.en.json`) or a part of it. */
export interface OidcWording {
  [key: string]: string | OidcWording
}

function overlay(base: OidcWording, over: OidcWording): OidcWording {
  const merged: OidcWording = { ...base }
  for (const [key, value] of Object.entries(over)) {
    const below = merged[key]
    merged[key] = typeof value === 'object' && typeof below === 'object' ? overlay(below, value) : value
  }
  return merged
}

/**
 * The words of a wording file as the app merges them into its resources. Apps with one account (nexcrate, nexsift;
 * `AppConfig.single_account`) pass `singleAccount: true`: then the sentences under `oidc.single` take the place of
 * the keys of the same name (`oidc.admin.passwordHint`, `oidc.admin.addressHint`, `oidc.error.oidc_no_account`), so
 * the page asks for the same keys in every app. `oidc.single` itself is not handed on. Counts use i18next's plural
 * forms (`_one`, `_other`): ask for the key without the suffix and pass `count`.
 */
export function oidcWording(file: OidcWording, options: { singleAccount: boolean }): OidcWording {
  const root = file.oidc
  if (typeof root !== 'object') return { ...file }
  const { single, ...words } = root
  const chosen = options.singleAccount && typeof single === 'object' ? overlay(words, single) : words
  return { ...file, oidc: chosen }
}

const SPELLED_OUT: Record<string, string> = { ä: 'ae', ö: 'oe', ü: 'ue', ß: 'ss', Ä: 'Ae', Ö: 'Oe', Ü: 'Ue' }
const SLUG_MAX = 40

/**
 * The short name the form suggests for a name on the button, as the server does (`providers.slug_from_label`):
 * lower case, German letters written out, other accents dropped, everything else a dash, at most 40 characters.
 */
export function slugFromLabel(label: string): string {
  const spelled = label.replace(/[äöüßÄÖÜ]/g, (letter) => SPELLED_OUT[letter] ?? letter)
  // Only what ASCII has, as the server's encode('ascii', 'ignore'); a filter instead of a pattern with control
  // characters in it (eslint no-control-regex).
  const ascii = Array.from(spelled.normalize('NFKD'))
    .filter((char) => char.charCodeAt(0) < 128)
    .join('')
  const slug = ascii
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug.slice(0, SLUG_MAX).replace(/-+$/, '')
}

/** What the server returns. Field names match the responses (snake_case). */

export type Role = 'operator' | 'member'
export type SignIn = 'password' | 'oidc'
export type VaultState = 'open' | 'locked' | 'unset'

export interface Account {
  id: number
  name: string
  role: Role
  sign_in: SignIn
  email: string
  two_factor: boolean
  /** An authenticator app is set up. */
  totp: boolean
  /** How many passkeys the account has. */
  passkeys: number
  /** Unused recovery codes; 0 without a second factor. */
  two_factor_recovery_left: number
  /** The operator requires a second factor and this account has none yet: only the account page is open. */
  second_factor_setup_required: boolean
  /** Linked to an identity at the OIDC provider. */
  oidc_linked: boolean
  vault: VaultState
  prefs: Record<string, unknown>
  created_at: string
  last_seen_at: string | null
  /** A guest account ends here; null for no end. */
  expires_at: string | null
}

/** The password step of a sign-in with a second factor: nothing is open yet. */
export type SecondFactorMethod = 'totp' | 'passkey' | 'recovery'

export interface SecondFactorPending {
  second_factor: true
  /** What the second step offers; older servers send nothing, which means the app. */
  methods?: SecondFactorMethod[]
}

export interface PasskeyInfo {
  id: number
  name: string
  created_at: string
  last_used_at: string | null
  /** The first characters of the credential id, to tell two keys apart. */
  credential: string
}

export interface TotpEnrolment {
  secret: string
  uri: string
  qr_svg: string
}

export interface RecoveryCodes {
  recovery_codes: string[]
  account: Account
}

export interface SetupState {
  needs_setup: boolean
  version: string
  min_password: number
}

export interface AccountName {
  id: number
  name: string
}

export interface InviteInfo {
  id: number
  name: string
  role: Role
  expires_at: string
  /** The account made from it is a guest until then. */
  account_expires_at: string | null
  link?: string
}

export type AuthMethod = 'key' | 'password' | 'ask'
export type HostKeyState = 'known' | 'new'

export interface HostKeyInfo {
  state: HostKeyState
  fingerprint: string
  key_type: string
}

export interface Connection {
  id: number
  name: string
  group: string
  host: string
  port: number
  /** For a shared connection, the own user; otherwise the owner's. */
  user: string
  owner_user: string
  auth: AuthMethod
  key_id: number | null
  jump_id: number | null
  keepalive: boolean
  start_command: string
  owner_id: number
  shared_by: string | null
  shared_with: number[]
  /** For the owner: when each share ends, by account id; null for no end. */
  share_ends: Record<string, string | null>
  /** For a member: when the own share ends. */
  share_expires_at: string | null
  host_key: HostKeyInfo
  last_used_at: string | null
  created_at: string
}

export interface ConnectionIn {
  name: string
  group: string
  host: string
  port: number
  user: string
  auth: AuthMethod
  key_id: number | null
  jump_id: number | null
  keepalive: boolean
  start_command: string
}

export type Reach = 'up' | 'down' | 'unknown'

export interface ReachInfo {
  reach: Reach
  latency_ms: number | null
  blocked?: string
}

export interface VaultKey {
  id: number
  name: string
  key_type: 'ed25519' | 'rsa' | 'ecdsa'
  bits: number
  fingerprint: string
  public_key: string
  has_passphrase: boolean
  created_at: string
  used_by: number
}

export interface VaultPassword {
  connection_id: number
  changed_at: string
}

export interface RunningSession {
  id: string
  account: string
  /** The viewer's own session: only these can be taken into a browser. */
  mine: boolean
  /** Open, but no browser watches it; it waits for one to come back. */
  detached: boolean
  connection_id: number | null
  name: string
  target: string
  started_at: string
  from_ip: string
  state: 'open' | 'connecting'
}

export type SessionEnd = 'running' | 'normal' | 'failed' | 'hostkey' | 'cut'

export interface SessionRecord {
  id: number
  account: string
  connection_id: number | null
  name: string
  target: string
  from_ip: string
  started_at: string
  ended_at: string | null
  end: SessionEnd
  detail: string
}

export interface FileEntry {
  name: string
  type: 'file' | 'dir' | 'link'
  size: number
  mtime: number | null
  /** Permissions as a number (0o755), null if the server does not report them. */
  mode: number | null
}

export interface FileListing {
  path: string
  home: string
  entries: FileEntry[]
}

export type TargetsMode = 'private' | 'list' | 'all'
export type BackupSchedule = 'off' | 'daily' | 'weekly' | 'monthly'

export interface Settings {
  targets_mode: TargetsMode
  targets_list: string[]
  vault_lock_minutes: number
  /** How long an open shell waits for its browser to come back; 0 ends it with the browser. */
  detach_minutes: number
  /** Notifications to nexsift, Gotify, ntfy or a webhook; a way out, closed by default. */
  notify_enabled: boolean
  notify_kind: NotifyKind
  notify_url: string
  /** Whether a token is stored; the token itself never comes back. */
  notify_token_set: boolean
  notify_events: NotifyCategory[]
  history_days: number
  update_check: boolean
  /** Read-only API keys for dashboards; closed until the operator opens it. */
  api_keys_allowed: boolean
  backup_schedule: BackupSchedule
  backup_keep: number
  password_login: boolean
  two_factor_required: boolean
  oidc_auto_create: boolean
  /** The address people use to reach nextrmnl; empty means the environment or the request decides. */
  public_url: string
}

export interface ApiKeyInfo {
  id: number
  name: string
  /** The first characters, to tell keys apart; the key itself is never shown again. */
  prefix: string
  created_by: string
  created_at: string
  last_used_at: string | null
}

export interface ApiKeyList {
  allowed: boolean
  keys: ApiKeyInfo[]
}

export interface ApiKeyCreated extends ApiKeyInfo {
  key: string
}

export interface AboutInfo {
  version: string
  license: string
  repo_url: string
  release_url: string
  update_check: boolean
  update_checked: boolean
  checked_at: string | null
  latest_version: string | null
  update_available: boolean
}

export interface LogEntry {
  time: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  logger: string
  message: string
  request_id: string | null
  user: string | null
}

export type LogMode = 'quiet' | 'normal' | 'detailed' | 'trace'

export interface LogModeState {
  mode: LogMode
  until: string | null
  fixed_by_env: boolean
  modes: LogMode[]
  durations: number[]
}

export type BackupKind = 'auto' | 'update' | 'manual'

export interface Backup {
  name: string
  size: number
  created: string
  kind: BackupKind
  note: string
  version: string
  compatible: boolean
  reason: string
}

export interface BackupList {
  entries: Backup[]
  folder: string
  schedule: BackupSchedule
  keep: number
}

export interface BackupBrief {
  version: string
  created: string
  kind: BackupKind
  note: string
  accounts: number
  connections: number
  key_in_archive: boolean
  key_from_env: boolean
  compatible: boolean
  reason: string
}

export interface VaultFileBrief {
  account: string
  server: string
  created: string
  keys: number
  passwords: number
}

export interface VaultImportResult {
  added_keys: number
  skipped_keys: number
  added_passwords: number
  skipped_passwords: number
}

export interface OidcState {
  enabled: boolean
  provider_name: string
}

export interface OidcConfig {
  configured: boolean
  issuer: string
  client_id: string
  provider_name: string
  redirect_uri: string
}

export type AuthentikStepKey = 'reached' | 'signingKey' | 'mapping' | 'provider' | 'application' | 'filled'

export interface AuthentikStep {
  key: AuthentikStepKey
  ok: boolean
  detail: string
}

export interface AuthentikResult {
  steps: AuthentikStep[]
  client_id: string
  issuer: string
}

/** A command an account keeps at hand; only its owner sees it. */
export interface Snippet {
  id: number
  name: string
  command: string
  created_at: string
  last_used_at: string | null
}

export type NotifyKind = 'gotify' | 'ntfy' | 'webhook'
export type NotifyCategory = 'security' | 'signin' | 'sessions' | 'operations'

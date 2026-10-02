# nextrmnl

SSH and SFTP in the browser, self-hosted, for the machines in your own network. Accounts with a vault each,
host keys that are checked and remembered, and a terminal that copies and pastes the way PuTTY and Windows
Terminal do.

nextrmnl is part of the nex apps and looks like them. The project site with the full tour, in English and German,
is at [nextrmnl.nexapps.dev](https://nextrmnl.nexapps.dev).

## Screenshots

![Terminal with the connection list pinned to the left](docs/screenshots/terminal.png)

*The terminal, with the connection list pinned to the left. It floats above the terminal by default.*

![Files next to the terminal](docs/screenshots/files.png)

*Files over SFTP next to the terminal: browse, upload, download, rename, delete.*

![The vault with keys and stored passwords](docs/screenshots/vault.png)

*The vault: keys and stored passwords, sealed with the account's password.*

## What it does

- **Terminal in the browser** (xterm.js) over a WebSocket to the nextrmnl server, which speaks SSH to your
  machines. Full-screen, tabs for open sessions, jump hosts, a command to run after sign-in, keepalives.
- **Sessions that survive the browser**: a reload, a closed tab or a dropped network does not end the shell. It
  keeps running for a few minutes (5 by default, set by the operator, 0 turns it off) and comes back with its last
  screen; after a short drop only what was missed is sent again. Another device of the same account can take it
  over. Closing the tab on purpose ends it at once, and the browser asks before leaving a page with an open shell,
  so Ctrl+W in nano does not cost the session.
- **Split view**: two terminals side by side, above each other, or four; each field picks its session. **Type
  into all** sends the keyboard to every field at once, with red frames as long as it is on.
- **Commands** kept per account and typed into the terminal with a click, never run: Enter is yours.
- **Import** from `~/.ssh/config` (hosts, users, ports, ProxyJump) or a PuTTY registry export. Keys and passwords
  stay where they are.
- **On a phone**: a key bar with Esc, Tab, Ctrl, Alt and the arrows, and nextrmnl installs as an app.
- **Direct links** to a connection (`/connect/<id>`) for bookmarks and dashboards; opening one asks before it
  connects.
- **Search in the terminal** with Ctrl+Shift+F, all matches marked. **Color schemes** (Dracula, Nord, Solarized,
  Gruvbox, One Dark, Tokyo Night, or the nex colors that follow light and dark) and **any installed font**, Nerd
  Fonts included; changes apply to open terminals right away.
- **SFTP next to the terminal**: browse, upload with drag and drop, download, rename, delete, all through the
  same SSH connection.
- **Copy and paste like PuTTY**: selecting copies, Ctrl+Shift+C, Ctrl+Insert, Ctrl+C with a selection; paste with
  Ctrl+V, Ctrl+Shift+V, Shift+Insert or a right click. Several lines are shown before they run.
- **Accounts**: the first account is the operator, others come by invitation link or through OpenID Connect.
  Connections can be shared; sharing passes name, address and settings, never access.
- **Second factor**: a code from an authenticator app on top of the password (TOTP), a **passkey** or a security
  key such as a YubiKey (WebAuthn, on HTTPS), with recovery codes for the day the phone or the key is gone. The
  operator can require it for every password account; whoever locks themselves out gets it reset by the operator.
- **Guest accounts and shares with an end**: an invitation can make an account that ends on a day, and a share
  can end on a day too. Then sign-in, connection and open terminals end with it.
- **Notifications** to [nexsift](https://github.com/DerKezorm/nexsift), Gotify, ntfy or a webhook: locked
  accounts, changed host keys, sign-ins, opened sessions, failed backups, each group on or off. Off by default.
- **A vault per account** for private keys and stored passwords, encrypted with a key that only the account's
  password unwraps. Not the operator, not a backup, not a database dump can read it. Generate Ed25519 or RSA
  keys, or paste existing ones. Download the vault as an encrypted file and restore it, here or on another nextrmnl.
- **Host keys** are compared on every connection. Unknown hosts are shown with their fingerprint before you
  trust them; a changed key is refused until you say otherwise.
- **Allowed targets**: by default nextrmnl only connects into private networks (10/8, 172.16/12, 192.168/16,
  100.64/10 for VPNs, loopback, link-local). The operator can add networks and names, or open it up.
- **OpenID Connect**: any provider with discovery; for authentik there is a one-button setup that creates the
  provider, application, signing key and mappings with a one-time API token, or a blueprint file to do it
  without a token.
- **Backups**: consistent copies on a schedule and before every schema change, downloads as an AES encrypted
  ZIP that 7-Zip opens without nextrmnl, restore with a preview and a restart.
- **A log** with four levels (the deep ones switch themselves off), a request id in every line and in every
  error message, downloadable. Never with terminal content, keystrokes, passwords, keys or tokens.
- **Read-only API keys** for dashboards such as [nexdeck](https://nexdeck.nexapps.dev): four endpoints under
  `/api/v1` (`status`, `sessions`, `history`, `connections`) with the key as `Authorization: Bearer`. Off by
  default; the operator switches them on and creates them under Settings, Security. Each connection carries its
  direct link.
- German and English; another language is one JSON file.

## Start

```yaml
services:
  nextrmnl:
    image: ghcr.io/derkezorm/nextrmnl:latest
    container_name: nextrmnl
    restart: unless-stopped
    ports:
      - "8460:8000"
    volumes:
      - ./data:/data
    environment:
      PUID: 1000
      PGID: 1000
      TZ: Europe/Berlin
```

```
docker compose up -d
```

Open `http://<your-host>:8460` and create the operator account. The password is at least 12 characters; it also
protects your vault.

**Put nextrmnl behind a reverse proxy with TLS** before you use it from anywhere but your own desk. It holds the
access to your machines. Pasting with a right click also needs HTTPS; the browser allows reading the clipboard
only on secure origins (Ctrl+V works regardless).

## Where things are stored

Everything lives in `/data`: the SQLite database `nextrmnl.db`, `secret.key`, `backups/`, `logs/`. Mount it
from a local disk, never from an SMB or NFS share.

`secret.key` protects the server-side secrets (the OIDC client secret). The vaults do not depend on it: they
open with the account's password only. Both go into the backup archive.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `NEXTRMNL_DATA_DIR` | `/data` | Data directory |
| `NEXTRMNL_SECRET_KEY` | created on first start | Protects the server-side secrets; when set, it wins over `secret.key` |
| `NEXTRMNL_PUBLIC_URL` | from the request | The address people use to reach nextrmnl; for invitation links and the OIDC redirect. The public address under Settings, Sign-in wins when set |
| `NEXTRMNL_LOG_LEVEL` | stored setting | `quiet`, `normal`, `detailed` or `trace`; overrides the setting, the emergency exit when the app does not start |
| `NEXTRMNL_TRUSTED_PROXIES` | none | Addresses or networks of reverse proxies whose `X-Forwarded-For` is believed, comma separated (`172.18.0.0/16, 10.0.0.5`). Without it every request counts as coming from its peer, so a proxy in front makes all its clients share one sign-in brake |
| `NEXTRMNL_SESSION_DAYS` | `14` | A browser session ends after this many days |
| `NEXTRMNL_API_DOCS` | `false` | Serves `/api/docs` and `/api/openapi.json`; off by default |
| `NEXTRMNL_COOKIE_SECURE` | `auto` | `on`, `off` or `auto` (from the request or `X-Forwarded-Proto`) |
| `NEXTRMNL_PORT` | `8000` | Port inside the container, for host networking |
| `PUID`, `PGID` | `1000` | Owner of the files in the data directory |

## Security in short

- Passwords are hashed with Argon2id. Ten failed sign-ins in a row lock the account for fifteen minutes.
- With a second factor, the password alone opens nothing: the vault key waits in memory for the code, at most
  five minutes and five tries. A code counts once; the seed is stored encrypted, recovery codes as hashes.
- The vault key is wrapped with Argon2id and AES-256-GCM; entries are AES-256-GCM. An open vault is its key in
  the server's memory, forgotten after 30 minutes without use (configurable) and on every restart.
- Every changing request needs the header `X-Requested-By: nextrmnl`; WebSockets must come from the same origin.
- Every password check while signed in (opening the vault, exporting it, changing the password, the second
  factor) counts like a sign-in: wrong answers lock the account. Terminals end with the sign-in they came with.
- A shell waiting for its browser can only be taken back by the account that opened it, not even by the operator.
  Its last screen (at most 256 KB) stays in the server's memory only, never on disk, in the log or in a backup, and
  is gone with the session. Signing out ends waiting shells too.
- A passkey is a second factor, never a replacement for the password: the vault opens with the password alone.
  Only the public key is stored; a key whose counter goes backwards (a copy) is refused.
- Notifications are another way out and closed until the operator opens them. Their token is stored encrypted and
  never shown again; redirects are not followed; a message never carries terminal content, passwords or keys.
- A direct link never opens a shell by itself: it shows the connection and waits for a click, and only to an
  account that sees that connection. Commands are text without control characters, so a stored command cannot
  smuggle in a Ctrl+C or an escape sequence.
- A member of a shared connection signs in with their own user, key or password, and only their own start
  command runs in their shell; a stored password stays with the host, port and user it was given for.
- Responses carry a Content Security Policy, `X-Frame-Options: DENY` and friends.
- Host keys are verified before authentication; nothing is trusted automatically.
- API keys only read. A key is shown once and stored as a hash, works only as a Bearer header, and stops working
  when the switch goes off or its account is no longer the operator. It opens no terminal, no file and no vault,
  and what it reads carries no sender addresses.
- The log never contains terminal content, keystrokes, passwords, passphrases, private keys or tokens, and a
  test scans the code for the obvious mistakes.

## Development

```
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/uvicorn app.main:app --port 8460 --reload
cd frontend && npm ci && npm run dev
```

The frontend on port 5460 proxies `/api` to the backend. Tests: `pytest` in `backend`, `npx vitest run` in
`frontend`.

## License

AGPL-3.0.

"""nextrmnl's side of the shared sign-in module ``vendor/nexoidc``: the ``Store`` over its tables, the start-up
configuration, and the migration from the single provider of the settings to the list.

The module (the shared blueprint "Anmeldung mit OIDC und authentik", the same in every nex app) runs the protocol,
decides who an identity is and keeps the provider list; it never sees a database. What is nextrmnl's own lives here:
accounts and their names, guest accounts that ran out, the role and end an invitation brings, the address an account
through a provider follows, and the encryption of the client secrets with ``secret.key``.

The vault is never touched here. An account that signs in through a provider has no password, so its vault stays
locked until the person chooses a vault password (``vault_ready`` false for a new one).

A ``SqlStore`` works inside one SQLAlchemy session, one request. The module commits when an outcome is settled and
rolls back when it refuses after a write (a consumed invitation comes back that way).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import crypto
from ..config import get_settings
from ..db import SessionLocal
from ..models import (
    MEMBER,
    OPERATOR,
    SIGN_IN_OIDC,
    SIGN_IN_PASSWORD,
    Account,
    AuthSession,
    OidcLink,
    OidcProvider,
    Setting,
    utcnow,
)
from ..models import Invite as InviteRow
from ..vendor import nexoidc
from ..vendor.nexoidc import (
    AccountState,
    Invite,
    Link,
    Provider,
    ProviderValues,
    migrate,
    secret_context,
)
from . import accounts, settings_service

logger = logging.getLogger("nextrmnl.oidc")

APP_NAME = "nextrmnl"
#: Where the module sends the browser: the sign-in page, the account page (a route of the frontend that opens the
#: account tab of the settings), the start page.
LOGIN_PAGE = "/login"
ACCOUNT_PAGE = "/account"
HOME = "/"
#: Longest account name (``accounts.NAME_PATTERN``).
NAME_MAX = 64
_NUMBERED = re.compile(r"^(.*?)(-\d+)$")


def _public_url() -> str:
    with SessionLocal() as db:
        return settings_service.public_url(db)


def _save_public_url(url: str) -> None:
    """The address the operator confirmed in the authentik card: stored in the setting Settings, Sign-in shows (the
    setting wins over ``NEXTRMNL_PUBLIC_URL``) and committed at once. The same check as the settings page makes:
    an address with a path is none here, and ``PublicUrlNotKept`` says so to the module."""
    clean = settings_service.normalize_public_url(url)
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": clean})


def _server_secret() -> str:
    return get_settings().resolved_secret_key()


def configure() -> None:
    """Tell the module who it runs in; called once at start-up (and by the tests)."""
    nexoidc.configure(
        nexoidc.AppConfig(
            app_name=APP_NAME,
            server_secret=_server_secret,
            public_url=_public_url,
            save_public_url=_save_public_url,
            login_page=LOGIN_PAGE,
            account_page=ACCOUNT_PAGE,
            home=HOME,
        )
    )


def clean_name(raw: str) -> str:
    """An account name from what the provider suggests: lower case, nextrmnl's characters, at most 64; a number at
    the end (``max-2``) survives the cut. Empty when nothing usable is left."""
    cleaned = re.sub(r"[^a-z0-9._-]", "-", raw.strip().lower()).strip("-._")
    if len(cleaned) > NAME_MAX:
        numbered = _NUMBERED.match(cleaned)
        suffix = numbered.group(2) if numbered else ""
        stem = numbered.group(1) if numbered else cleaned
        cleaned = stem[: NAME_MAX - len(suffix)].rstrip("-._") + suffix
    return cleaned if accounts.NAME_PATTERN.match(cleaned) else ""


def has_links(db: Session, account_id: int) -> bool:
    return db.scalar(select(OidcLink.id).where(OidcLink.account_id == account_id).limit(1)) is not None


def provider_only(account: Account) -> bool:
    """The account signs in through providers only: it has no password here."""
    return account.sign_in != SIGN_IN_PASSWORD or not account.password_hash


def follow_address(db: Session, account: Account, email: str | None) -> None:
    """The address a provider sends, as nextrmnl treats it. nextrmnl has no address of its own to change, so only an
    account that signs in through providers alone follows it; an account with a password is not offered it (there is
    nothing it would be offered for). Never an address another account has. Nothing is committed here."""
    email = (email or "").strip()
    if not email or len(email) > 255 or not provider_only(account) or email.lower() == (account.email or "").lower():
        return
    db.flush()
    taken = db.scalar(select(Account.id).where(func.lower(Account.email) == email.lower(), Account.id != account.id))
    if taken is not None:
        logger.warning("Address from the provider not taken, another account has it name=%s", account.name)
        return
    account.email = email


def forget_provider_address(db: Session, account_id: int) -> None:
    """No provider left for the account: its address goes. Every address in nextrmnl came from a provider (there is
    no other way to set one), and the version before took it away with the link as well."""
    if has_links(db, account_id):
        return
    account = db.get(Account, account_id)
    if account is not None and account.email:
        account.email = ""
        logger.info("Address from the provider went with the last link name=%s", account.name)


class SqlStore:
    """``nexoidc.Store`` over ``oidc_providers`` and ``oidc_links``, inside one session."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Providers -------------------------------------------------------------------------------------------------

    def _provider(self, row: OidcProvider | None) -> Provider | None:
        if row is None:
            return None
        return Provider(
            id=row.id,
            slug=row.slug,
            label=row.label,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret=crypto.decrypt_secret(row.client_secret_enc, secret_context(row.id)),
            scopes=row.scopes,
            enabled=row.enabled,
            auto_create=row.auto_create,
            trusts_second_factor=row.trusts_second_factor,
            managed=row.managed,
            position=row.position,
            redirect_path=row.redirect_path,
            created_at=row.created_at,
            registered_redirect=row.registered_redirect or "",
        )

    def list_providers(self) -> list[Provider]:
        rows = self.db.scalars(select(OidcProvider).order_by(OidcProvider.position, OidcProvider.id))
        return [provider for provider in (self._provider(row) for row in rows) if provider is not None]

    def get_provider(self, provider_id: int) -> Provider | None:
        return self._provider(self.db.get(OidcProvider, provider_id))

    def provider_by_slug(self, slug: str) -> Provider | None:
        return self._provider(self.db.scalar(select(OidcProvider).where(OidcProvider.slug == slug)))

    @staticmethod
    def _apply(row: OidcProvider, values: ProviderValues) -> None:
        row.slug, row.label, row.issuer, row.client_id = values.slug, values.label, values.issuer, values.client_id
        row.scopes, row.enabled, row.auto_create = values.scopes, values.enabled, values.auto_create
        row.trusts_second_factor, row.managed, row.position = (
            values.trusts_second_factor,
            values.managed,
            values.position,
        )
        row.redirect_path = values.redirect_path
        if values.registered_redirect is not None:
            # None keeps what the button registered last; only the button writes it.
            row.registered_redirect = values.registered_redirect

    def insert_provider(self, values: ProviderValues) -> Provider:
        row = OidcProvider()
        self._apply(row, values)
        self.db.add(row)
        self.db.flush()
        row.client_secret_enc = crypto.encrypt_secret(values.client_secret or "", secret_context(row.id))
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def update_provider(self, provider_id: int, values: ProviderValues) -> Provider:
        row = self.db.get(OidcProvider, provider_id)
        assert row is not None
        self._apply(row, values)
        if values.client_secret is not None:
            row.client_secret_enc = crypto.encrypt_secret(values.client_secret, secret_context(row.id))
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def delete_provider(self, provider_id: int) -> None:
        # Foreign keys are on (db.py), the links would go anyway; said here so that nothing depends on a pragma.
        self.drop_links(provider_id)
        self.db.execute(delete(OidcProvider).where(OidcProvider.id == provider_id))

    # --- Links -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _link(row: OidcLink) -> Link:
        return Link(
            row.provider_id, row.subject, row.account_id, row.issuer, row.email, row.created_at, row.last_used_at
        )

    def find_link(self, provider_id: int, subject: str) -> Link | None:
        row = self.db.scalar(select(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject))
        return self._link(row) if row else None

    def links_of_provider(self, provider_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.provider_id == provider_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def links_of_account(self, account_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.account_id == account_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def add_link(self, link: Link) -> None:
        self.db.add(
            OidcLink(
                provider_id=link.provider_id,
                subject=link.subject,
                account_id=link.account_id,
                issuer=link.issuer,
                email=link.email,
            )
        )
        self.db.flush()
        self._address(link.account_id, link.email)

    def remove_link(self, provider_id: int, account_id: int) -> bool:
        result = self.db.execute(
            delete(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.account_id == account_id)
        )
        removed = bool(result.rowcount)  # type: ignore[attr-defined]
        if removed:
            forget_provider_address(self.db, account_id)
        return removed

    def drop_links(self, provider_id: int) -> int:
        holders = set(self.db.scalars(select(OidcLink.account_id).where(OidcLink.provider_id == provider_id)))
        result = self.db.execute(delete(OidcLink).where(OidcLink.provider_id == provider_id))
        for account_id in holders:
            forget_provider_address(self.db, account_id)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def touch_link(self, provider_id: int, subject: str, email: str | None) -> None:
        self.db.execute(
            update(OidcLink)
            .where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
            .values(last_used_at=utcnow(), email=email)
        )
        account_id = self.db.scalar(
            select(OidcLink.account_id).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
        )
        if account_id is not None:
            self._address(account_id, email)

    def _address(self, account_id: int, email: str | None) -> None:
        account = self.db.get(Account, account_id)
        if account is not None:
            follow_address(self.db, account, email)

    # --- Accounts --------------------------------------------------------------------------------------------------

    def account_state(self, account_id: int) -> AccountState | None:
        row = self.db.get(Account, account_id)
        if row is None:
            return None
        from . import totp

        # A guest account that ran out is blocked, through a provider as with a password. The lock after wrong
        # passwords is not: it keeps out guessing at the password, and a stranger could set it off from outside.
        return AccountState(
            id=row.id,
            name=row.name,
            blocked=row.expired,
            has_password=row.sign_in == SIGN_IN_PASSWORD and bool(row.password_hash),
            has_second_factor=totp.has_second_factor(self.db, row),
        )

    def clean_name(self, raw: str) -> str:
        return clean_name(raw)

    def name_taken(self, name: str) -> bool:
        return accounts.by_name(self.db, name) is not None

    def create_account(self, name: str, *, invite: Invite | None, email: str | None) -> int:
        invited = invite.rights if invite is not None and isinstance(invite.rights, InviteRow) else None
        role = invited.role if invited is not None else MEMBER
        row = Account(name=name, role=role, sign_in=SIGN_IN_OIDC)
        if invited is not None and role != OPERATOR:
            # A guest invitation makes a guest account, whichever way the person comes in.
            row.expires_at = invited.account_expires_at
        self.db.add(row)
        self.db.flush()
        follow_address(self.db, row, email)
        logger.info("Account created name=%s role=%s sign_in=oidc", row.name, role)
        return row.id

    # --- Invitations -----------------------------------------------------------------------------------------------

    def find_invite(self, key: str) -> Invite | None:
        row = accounts.find_invite(self.db, key)
        return Invite(key=key, id=row.id, rights=row) if row is not None else None

    def consume_invite(self, invite: Invite) -> bool:
        return accounts.consume_invite(self.db, invite.id)

    def accept_invite(self, account_id: int, invite: Invite) -> None:
        """An account that is known already takes an invitation it came through: the higher role of the two, and no
        end or the later one. Adds, never takes away."""
        row = self.db.get(Account, account_id)
        invited = invite.rights if isinstance(invite.rights, InviteRow) else None
        if row is None or invited is None:
            return
        if invited.role == OPERATOR:
            row.role, row.expires_at = OPERATOR, None
        elif row.expires_at is not None:
            later = invited.account_expires_at
            row.expires_at = None if later is None else max(row.expires_at, later)
        logger.info("Invitation accepted by a known account name=%s role=%s", row.name, row.role)

    # --- Transaction -----------------------------------------------------------------------------------------------

    def commit(self) -> None:
        # Whatever the module settled, the way back to the version before the list sees the same.
        mirror_legacy(self.db)
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


# --- What the routers and the account list need -----------------------------------------------------------------


def linked_providers(db: Session) -> dict[int, list[dict[str, object]]]:
    """Per account the providers it is linked to, for the marks in the operator's account list."""
    rows = db.execute(
        select(OidcLink.account_id, OidcProvider.id, OidcProvider.slug, OidcProvider.label)
        .join(OidcProvider, OidcProvider.id == OidcLink.provider_id)
        .order_by(OidcProvider.position, OidcProvider.id)
    )
    out: dict[int, list[dict[str, object]]] = {}
    for account_id, provider_id, slug, label in rows:
        out.setdefault(account_id, []).append({"id": provider_id, "slug": slug, "label": label})
    return out


def trusted_accounts(db: Session) -> set[int]:
    """The accounts that are linked to an entry that is on and trusted with the second factor."""
    rows = db.scalars(
        select(OidcLink.account_id)
        .join(OidcProvider, OidcProvider.id == OidcLink.provider_id)
        .where(OidcProvider.enabled.is_(True), OidcProvider.trusts_second_factor.is_(True))
    )
    return set(rows)


def withdraw_trust(db: Session, account_ids: set[int]) -> None:
    """The browser sessions of these accounts no longer count as checked by a provider (``via_trusted_provider``):
    the entry stopped being trusted, went off or away, or the account let go of it. They are held to the requirement
    for a second factor again, as a sign-in with the password is."""
    if account_ids:
        db.execute(
            update(AuthSession)
            .where(AuthSession.account_id.in_(account_ids), AuthSession.via_trusted_provider.is_(True))
            .values(via_trusted_provider=False)
        )
        db.commit()


@contextmanager
def watching_trust(db: Session) -> Iterator[None]:
    """Around whatever can end the trust some sessions rest on: whoever was linked to a trusted entry before and is
    not after loses the exemption of their sessions (see ``withdraw_trust``)."""
    before = trusted_accounts(db)
    yield
    withdraw_trust(db, before - trusted_accounts(db))


def link_count(db: Session, account_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(OidcLink).where(OidcLink.account_id == account_id)) or 0)


def any_active(db: Session) -> bool:
    return db.scalar(select(OidcProvider.id).where(OidcProvider.enabled.is_(True)).limit(1)) is not None


def unlinked_notices(account: Account) -> list[str]:
    try:
        names = json.loads(account.oidc_unlinked or "[]")
    except ValueError:
        return []
    return [str(name) for name in names if isinstance(name, str)][:20] if isinstance(names, list) else []


def note_unlinked(account: Account, label: str) -> None:
    """The operator took one of the account's links: the account sees it on its own page until it says it saw it."""
    names = [name for name in unlinked_notices(account) if name != label]
    account.oidc_unlinked = json.dumps([*names, label][-20:])


# --- The migration from the single provider of the settings (blueprint 01 "Umstieg", 06 "Wanderung") --------------

#: Set once the settings were looked at: an operator who later removes every entry must not get the old provider
#: back at the next start.
MIGRATED = "oidc_list_migrated"
#: What the way back wrote last, as the module sums it up (``migrate.fingerprint``): the next start takes over what
#: the version before the list changed since, and nothing else.
FINGERPRINT = "oidc_legacy_fingerprint"
#: The version before the list asked no code of its own after the provider (blueprint 06 "Wanderung").
TRUSTED_BEFORE = True
#: The issuer the version before the list knows, as it wrote it, or ``migrate.CLOSED`` (blueprint 01 "Umstieg" 4):
#: ``migrate.way_back`` decides from it and the entry ``oidc`` what the old settings show. The app stores it and
#: never reads into it.
ANCHOR = "oidc_legacy_anchor"


def read_legacy(db: Session) -> tuple[migrate.Legacy, list[tuple[int, str]]]:
    """The old settings and the old column, read the one way the migration, the fingerprint and ``resync`` read them.
    The secret is the opened one: sealed afresh every time, the stored text would give another fingerprint."""
    values = settings_service.get_all(db)
    label = str(values.get("oidc_provider_name") or "")
    legacy = migrate.Legacy(
        issuer=str(values.get("oidc_issuer") or ""),
        client_id=str(values.get("oidc_client_id") or ""),
        client_secret=crypto.decrypt_secret(str(values.get("oidc_client_secret_enc") or "")),
        label=label,
        auto_create=bool(values.get("oidc_auto_create")),
        # The authentik button of nextrmnl 0.1 to 0.4 named the provider "authentik"; the form never did by itself.
        set_up_by_button=label == "authentik",
    )
    subjects = [
        (account_id, subject)
        for account_id, subject in db.execute(
            select(Account.id, Account.oidc_subject).where(Account.oidc_subject != "").order_by(Account.id)
        )
    ]
    return legacy, subjects


def legacy_plan(db: Session) -> migrate.Plan | None:
    """The entry ``oidc`` and its links the old settings become; None when no provider was set up."""
    legacy, subjects = read_legacy(db)
    return migrate.plan(legacy, subjects, trusts_second_factor=TRUSTED_BEFORE)


def mirror_legacy(db: Session) -> None:
    """The way back to nextrmnl 0.4 (blueprint 01 "Umstieg" 4), inside every commit that changes accounts, links or
    the entry: that version reads the old settings ``oidc_*`` and the column ``oidc_subject``, so they must always
    show what ``migrate.way_back`` gives for the entry ``oidc`` and the stored anchor (one rule for every app, see the
    module). Otherwise going back would bring a removed provider back, or let a subject into an account that gave it
    up. The issuer is written as the anchor holds it, letter by letter: the version before compares it that way.

    Last the fingerprint of what stands there, for ``migrate.resync`` at the next start. Nothing is committed here;
    the store calls it before every commit, and so does every other way that changes accounts (deleting one:
    ``routers/auth.delete_account``)."""
    db.flush()
    store = SqlStore(db)
    entry = store.provider_by_slug(nexoidc.LEGACY_SLUG)
    links = [(link.account_id, link.subject) for link in store.links_of_provider(entry.id)] if entry else []
    mirror = migrate.way_back(entry, links, str(settings_service.get(db, ANCHOR) or "") or None)
    shown = mirror.legacy
    changes: dict[str, object] = {
        "oidc_issuer": shown.issuer,
        "oidc_client_id": shown.client_id,
        "oidc_provider_name": shown.label,
        "oidc_auto_create": bool(shown.auto_create),
        ANCHOR: mirror.anchor,
    }
    # Sealed afresh every time, so only when the secret itself differs.
    if crypto.decrypt_secret(str(settings_service.get(db, "oidc_client_secret_enc") or "")) != shown.client_secret:
        changes["oidc_client_secret_enc"] = crypto.encrypt_secret(shown.client_secret) if shown.client_secret else ""
    put_settings(db, {key: value for key, value in changes.items() if settings_service.get(db, key) != value})
    wanted_ids = list(mirror.subjects)
    for account in db.scalars(select(Account).where((Account.oidc_subject != "") | Account.id.in_(wanted_ids))):
        wanted = mirror.subjects.get(account.id, "")
        if account.oidc_subject != wanted:
            account.oidc_subject = wanted
    db.flush()
    legacy, subjects = read_legacy(db)
    put_settings(db, {FINGERPRINT: migrate.fingerprint(legacy, subjects)})


def put_settings(db: Session, changes: dict[str, object]) -> None:
    """Settings written in the open transaction (``settings_service.save`` commits on its own)."""
    for key, value in changes.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value


def migration_due(db: Session) -> bool:
    return (
        not settings_service.get(db, MIGRATED)
        and db.scalar(select(func.count()).select_from(OidcProvider)) == 0
        and legacy_plan(db) is not None
    )


def _older_version_changed(db: Session) -> bool:
    """The old settings or subjects differ from what the last mirror wrote: the version before the list was at work.
    Compared in the form the stored fingerprint has, so that one of module 1.0.9 asks for no backup of its own."""
    legacy, subjects = read_legacy(db)
    previous = str(settings_service.get(db, FINGERPRINT) or "") or None
    return migrate.changes_since(previous, legacy, subjects).any


def migrate_settings(*, backup: bool = True) -> Provider | None:
    """What runs at every start.

    Once, the first time with the list: the provider of the settings becomes the entry ``oidc`` with a link per stored
    subject; ``/api/oidc/callback`` keeps signing in. A backup goes first (the way back). Nothing happens when there
    was no provider or the list has entries already.

    After that, at each start, what the version before the list changed in the old settings since the last mirror (an
    operator who went back and came up again) is taken over (``migrate.resync``), with a backup first when there is
    something to take. Then the mirror, the anchor and the fingerprint are written again, always, also without any
    provider: a provider the older version sets up later must be seen at the next start, not thrown away. Returns the
    entry the first migration made, else None."""
    with SessionLocal() as db:
        due = migration_due(db)
        if not due and not settings_service.get(db, MIGRATED):
            settings_service.save(db, {MIGRATED: True})
        changed = not due and _older_version_changed(db)
    if backup and (due or changed):
        from . import backups

        note = "before the sign-in provider list" if due else "before taking over the older version's sign-in settings"
        backups.create(kind=backups.UPDATE, note=note)
    with SessionLocal() as db:
        store = SqlStore(db)
        entry = None
        legacy, subjects = read_legacy(db)

        def keep_anchor(value: str) -> None:
            put_settings(db, {ANCHOR: value})

        if due and migration_due(db):
            # The anchor first: the commit of the migration writes the way back and must already see it.
            keep_anchor(migrate.anchor_of(legacy))
            entry = migrate.apply(store, legacy_plan(db))
            put_settings(db, {MIGRATED: True})
        else:
            previous = str(settings_service.get(db, FINGERPRINT) or "") or None
            with watching_trust(db):
                done = migrate.resync(
                    store,
                    legacy,
                    subjects,
                    previous=previous,
                    trusts_second_factor=TRUSTED_BEFORE,
                    anchor=str(settings_service.get(db, ANCHOR) or "") or None,
                    keep_anchor=keep_anchor,
                )
            if done.wrote:
                logger.info("Sign-in settings of the older version taken over")
        # Through the store: the mirror, the anchor and the new fingerprint are written in the same commit.
        store.commit()
        return entry

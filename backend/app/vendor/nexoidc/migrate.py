"""The way from one provider in the settings to the list (Bauplan 01 "Umstieg", 06 "Wanderung").

nexlore, nexcanvas, nextasks, nexsuite, nexbrand, nexdiary, nexpaper, nexsift and nextrmnl keep one provider in their
settings (``oidc_issuer``, ``oidc_client_id`` …) and the subject at the account (``oidc_subject``). At the first start
with the module, inside the app's own migration and after its backup, these become:

1. an entry with the slug ``oidc``: name as before, ``auto_create`` as before, ``trusts_second_factor`` as the app
   behaved before (on for the apps that asked no code after the provider, off for nexdiary and nexpaper),
   ``managed = "authentik"`` when the issuer ends in ``/application/o/<slug>/`` and the button had set it up;
2. a link per stored subject (``issuer`` = the issuer so far);
3. the old callback ``<api>/oidc/callback`` stays valid for ever and belongs to that entry (``flow.callback_path``).

The bridge over a confirmed address is gone for everybody at once: accounts that found each other through it have
their subject stored already and move along. The old settings and the column stay readable for one version (the
app's business), then they go.

``plan`` is pure; ``apply`` writes the plan through the ``Store`` once (a second run finds the entry and does
nothing).

**Up again after the way back** (since 1.0.9). While the old settings stay, the app mirrors the entry ``oidc`` into
them after every change (the way back). An operator may go back to the older version, change something there and
come up again; the list would never see that change and the next mirror would overwrite it. So the app keeps a
``fingerprint`` of what its mirror wrote, and at start-up ``resync`` takes over what the older version changed
since: only the parts whose fingerprint differs, nothing else. The fingerprint holds hashes only, never a secret or a
subject in the clear.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from . import config, coupling, model, protocol, providers
from .model import LEGACY_SLUG, MANAGED_NEXSUITE, Link, Provider, ProviderValues, Store

DEFAULT_LABEL = "OpenID Connect"
_AUTHENTIK_ISSUER = re.compile(r"/application/o/[-A-Za-z0-9_]+/?$")


def _log() -> logging.Logger:
    return logging.getLogger(config.current().log_name)


@dataclass(frozen=True)
class Legacy:
    """The single provider as the app stored it. ``set_up_by_button``: the authentik button wrote these settings
    (each app knows how it marked that, nexlore by the name "authentik")."""

    issuer: str
    client_id: str
    client_secret: str
    label: str = ""
    auto_create: bool = False
    set_up_by_button: bool = False
    #: The callback the provider knows when it is not ``<api>/oidc/callback`` (the app's old route), else "".
    redirect_path: str = ""


@dataclass(frozen=True)
class Plan:
    provider: ProviderValues
    links: list[tuple[int, str]] = field(default_factory=list)


def plan(legacy: Legacy, subjects: Iterable[tuple[int, str]], *, trusts_second_factor: bool) -> Plan | None:
    """The entry and the links the old settings become; None when no provider was set up (nothing to move)."""
    issuer = protocol.normalize_issuer(legacy.issuer)
    if not issuer or not legacy.client_id.strip():
        return None
    managed = (
        model.MANAGED_AUTHENTIK
        if legacy.set_up_by_button and _AUTHENTIK_ISSUER.search(legacy.issuer.strip())
        else model.MANAGED_HAND
    )
    values = ProviderValues(
        slug=LEGACY_SLUG,
        label=" ".join(legacy.label.split()) or DEFAULT_LABEL,
        issuer=issuer,
        client_id=legacy.client_id.strip(),
        client_secret=legacy.client_secret,
        scopes=model.DEFAULT_SCOPES,
        enabled=True,
        auto_create=legacy.auto_create and not config.current().single_account,
        trusts_second_factor=trusts_second_factor,
        managed=managed,
        position=0,
        redirect_path=providers.check_redirect_path(legacy.redirect_path),
    )
    seen: set[str] = set()
    links: list[tuple[int, str]] = []
    for account_id, subject in subjects:
        if not subject or not subject.strip() or subject in seen:
            continue
        seen.add(subject)
        links.append((account_id, subject))
    return Plan(provider=values, links=links)


def apply(store: Store, migration: Plan | None) -> Provider | None:
    """Write the plan once. Returns the new entry, or None when there was nothing to do (no old provider, or the entry
    ``oidc`` exists already)."""
    if migration is None or store.provider_by_slug(LEGACY_SLUG) is not None:
        return None
    entry = store.insert_provider(migration.provider)
    for account_id, subject in migration.links:
        store.add_link(Link(provider_id=entry.id, subject=subject, account_id=account_id, issuer=entry.issuer))
    store.commit()
    _log().warning(
        "OIDC: the provider from the settings became entry %s with %d links (issuer %s)",
        entry.slug,
        len(migration.links),
        entry.issuer,
    )
    return entry


def old_callback(template: str, slug: str) -> str:
    """The callback path a provider of an app with a list knew before the module, for that app's own migration of
    its table (Bauplan 01, last paragraph; 06 "Pfad der Schnittstelle"): nexbeat ``/api/auth/oidc/{slug}/callback``,
    nexdeck ``/api/v1/auth/oidc/{slug}/callback``, nexview, nexmail their own. The app stores it as the entry's
    ``redirect_path``; the start then sends it, the cookie follows it, and the app routes it to the same return."""
    return providers.check_redirect_path(template.format(slug=slug))


def apply_coupled(
    store: Store,
    *,
    nexsuite: Legacy,
    own: Legacy | None,
    people: Iterable[tuple[int, str]],
    own_subjects: Iterable[tuple[int, str]],
    trusts_second_factor: bool,
    label: str = "nexsuite",
) -> coupling.Coupling | None:
    """``trusts_second_factor`` is how the app behaved so far, for both entries: its own and the coupled one.

    The migration of an app that is coupled to nexsuite at the moment (Bauplan 06, Verbund): its own provider from
    before the coupling (the settings it saved for the way back, and the subjects it kept apart, nexcanvas
    ``oidc_subject_local``) becomes the entry ``oidc`` with its links, set aside at once; nexsuite becomes the coupled
    entry with the accounts' persons. Returns what the app keeps for uncoupling; None when there is a list already."""
    if store.list_providers():
        return None
    if own is not None:
        apply(store, plan(own, own_subjects, trusts_second_factor=trusts_second_factor))
    return coupling.couple(
        store,
        issuer=nexsuite.issuer,
        client_id=nexsuite.client_id,
        client_secret=nexsuite.client_secret,
        label=label,
        people=people,
        trusts_second_factor=trusts_second_factor,
    )


# ---------------------------------------------------------------------------
# Up again after the way back (fingerprint and resync)
# ---------------------------------------------------------------------------

#: The form of a fingerprint. A stored one of another form is not compared (nothing taken over, a new one is stored).
FINGERPRINT_FORM = "nexoidc-fp1"
#: Settings without a provider (no issuer or no client id): the older version lets nobody in through them, whatever
#: else stands there (nexlore keeps the issuer and the name of an entry that is off).
_NO_PROVIDER = "-"


def _hashed(kind: str, value: object) -> str:
    """128 bits of SHA-256 over a canonical form, base64url without padding: no character a fingerprint uses to
    separate its parts, and nothing the scanners take for a key. No key of the app goes in: a fingerprint must stay
    the same when the app's secret changes or a backup comes back, or a change that never happened would be taken
    over. The secret it covers is in the same database, encrypted, anyway."""
    canonical = json.dumps([FINGERPRINT_FORM, kind, value], ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("ascii")).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _has_provider(legacy: Legacy) -> bool:
    return bool(protocol.normalize_issuer(legacy.issuer)) and bool(legacy.client_id.strip())


def _settings_part(legacy: Legacy) -> str:
    if not _has_provider(legacy):
        return _NO_PROVIDER
    return _hashed(
        "settings",
        [
            protocol.normalize_issuer(legacy.issuer),
            legacy.client_id.strip(),
            # The secret only as a hash inside the hash: it never stands anywhere in the clear.
            hashlib.sha256(legacy.client_secret.encode("utf-8")).hexdigest(),
            " ".join(legacy.label.split()),
            bool(legacy.auto_create),
        ],
    )


def _subjects_of(subjects: Iterable[tuple[int, str]]) -> dict[int, str]:
    """Each account's old subject; empty ones are no subject. An account named twice keeps the first."""
    found: dict[int, str] = {}
    for account_id, subject in subjects:
        if subject and subject.strip() and int(account_id) not in found:
            found[int(account_id)] = subject
    return found


def fingerprint(legacy: Legacy, subjects: Iterable[tuple[int, str]]) -> str:
    """What the old settings and the old column say now, as the app reads them for ``plan`` (the same ``Legacy`` and
    the same subjects). Pure and stable: the same settings give the same fingerprint, in any order of the subjects.

    The app stores it after every mirror (inside the same transaction) and compares it at start-up. It holds one
    hash for the provider (issuer, client id, secret, name, ``auto_create``; or "no provider") and one per account
    with a subject, so that ``resync`` can tell which of them the older version changed."""
    accounts = ",".join(
        f"{account_id}:{_hashed('subject', subject)}" for account_id, subject in sorted(_subjects_of(subjects).items())
    )
    return f"{FINGERPRINT_FORM}|{_settings_part(legacy)}|{accounts}"


@dataclass(frozen=True)
class _Print:
    settings: str
    accounts: dict[int, str]


def _read(value: str | None) -> _Print | None:
    parts = (value or "").split("|")
    if len(parts) != 3 or parts[0] != FINGERPRINT_FORM or not parts[1]:
        return None
    accounts: dict[int, str] = {}
    for item in filter(None, parts[2].split(",")):
        account, _, digest = item.partition(":")
        if not account.isdigit() or not digest:
            return None
        accounts[int(account)] = digest
    return _Print(settings=parts[1], accounts=accounts)


@dataclass(frozen=True)
class Changes:
    """What the older version changed since the stored fingerprint. ``known`` False: there is no stored fingerprint
    of this form (first start with 1.0.9), nothing can be told and nothing is taken over."""

    known: bool = False
    settings: bool = False
    accounts: frozenset[int] = frozenset()

    @property
    def any(self) -> bool:
        return self.settings or bool(self.accounts)


def changes(previous: str | None, current: str) -> Changes:
    """Compare a stored fingerprint with the one of now. For an app that keeps the old settings somewhere else as
    well (nexcanvas ``oidc_way_back_held``): ``settings`` True means the older version wrote new settings, and they
    win over such a copy."""
    before, now = _read(previous), _read(current)
    if before is None or now is None:
        return Changes()
    touched = {
        account
        for account in before.accounts.keys() | now.accounts.keys()
        if before.accounts.get(account) != now.accounts.get(account)
    }
    return Changes(known=True, settings=before.settings != now.settings, accounts=frozenset(touched))


@dataclass
class Resync:
    """What ``resync`` did. ``coupled``: nothing, because the app is coupled to nexsuite (the list changes through the
    coupling only, Bauplan 06)."""

    changes: Changes = field(default_factory=Changes)
    coupled: bool = False
    removed: bool = False
    created: bool = False
    issuer_changed: bool = False
    values_taken: bool = False
    linked: list[int] = field(default_factory=list)
    unlinked: list[int] = field(default_factory=list)
    #: Accounts whose new old subject another account holds already: without a link at the entry now (their old one
    #: went, the older version gave it up); they are in ``unlinked`` as well when they had one.
    refused: list[int] = field(default_factory=list)

    @property
    def wrote(self) -> bool:
        changed = self.removed or self.created or self.issuer_changed or self.values_taken
        return changed or bool(self.linked or self.unlinked)


def _current_subject(store: Store, entry: Provider, account_id: int) -> str:
    return next((link.subject for link in store.links_of_account(account_id) if link.provider_id == entry.id), "")


def _take_subjects(store: Store, entry: Provider, wanted: dict[int, str], done: Resync) -> None:
    """Each account in ``wanted`` has at the entry now the subject the older version set ("" none).

    Two rounds, so that the order of the accounts decides nothing: first every link the older version gave up goes
    (emptied, or the account has another subject now), then the new subjects are linked. One round per account in turn
    found a subject that moved from bob to alex still at bob when alex came first, and left alex at the subject the
    older version had taken from him. An account whose new subject another account still holds loses its old link all
    the same (the older version gave it up) and gets none: a line in the log."""
    dropped: list[int] = []
    for account_id, subject in sorted(wanted.items()):
        current = _current_subject(store, entry, account_id)
        if current and current != subject and store.remove_link(entry.id, account_id):
            dropped.append(account_id)
    for account_id, subject in sorted(wanted.items()):
        if not subject or _current_subject(store, entry, account_id) == subject:
            continue
        holder = store.find_link(entry.id, subject)
        if holder is not None:
            done.refused.append(account_id)
            _log().warning(
                "OIDC: the older version gave account %d a subject of entry %s that account %d holds; account %d is "
                "left without a link there",
                account_id,
                entry.slug,
                holder.account_id,
                account_id,
            )
            continue
        if store.account_state(account_id) is None:
            continue
        store.add_link(Link(provider_id=entry.id, subject=subject, account_id=account_id, issuer=entry.issuer))
        done.linked.append(account_id)
    done.unlinked.extend(account_id for account_id in dropped if account_id not in done.linked)


def resync(
    store: Store,
    legacy: Legacy,
    subjects: Iterable[tuple[int, str]],
    *,
    previous: str | None,
    trusts_second_factor: bool,
) -> Resync:
    """At start-up, after the first migration, before the next mirror: take over what the older version changed in
    the old settings and the old column since the fingerprint ``previous`` was stored. ``legacy`` and ``subjects`` are
    read as for ``plan``; ``trusts_second_factor`` is how the older version behaved (as for ``plan``).

    Only what differs from the fingerprint counts, so an "empty" the mirror wrote itself (entry off, or the provider
    not checking the second factor) is no change. What changed:

    - the old settings now without a provider: the entry ``oidc`` goes, with its links;
    - a provider there and no entry ``oidc``: the entry is made as by the migration (its old callback);
    - another issuer: like an issuer change (the entry's links go, the older version's values come in);
    - the same issuer: client id, secret, name and ``auto_create`` are taken over;
    - either way, if the older version set a provider up where it had none, the entry is on again and trusts the
      provider as the older version did (there, a provider that is set up is on);
    - an account's old subject emptied: its link at the entry goes; set or changed: it is linked, unless another
      account holds that subject (then its old link goes all the same and it gets none, a line in the log). All links
      given up go first, then the new ones are made: a subject that moved from one account to another, or two that
      swapped, are taken over in any order of the accounts.

    While the app is coupled to nexsuite, nothing (``Resync.coupled``). Commits once when it wrote anything; the app's
    commit mirrors again, and the app stores the new fingerprint."""
    subjects = list(subjects)
    found = changes(previous, fingerprint(legacy, subjects))
    done = Resync(changes=found)
    if not found.any:
        return done
    entry = store.provider_by_slug(LEGACY_SLUG)
    if providers.coupled(store) or (entry is not None and entry.managed == MANAGED_NEXSUITE):
        done.coupled = True
        _log().warning("OIDC: the older version changed its sign-in settings while coupled; nothing taken over")
        return done
    olds = _subjects_of(subjects)
    touched = set(found.accounts)
    if found.settings:
        before = _read(previous)
        had_provider = before is not None and before.settings != _NO_PROVIDER
        wanted = plan(legacy, sorted(olds.items()), trusts_second_factor=trusts_second_factor)
        if wanted is None:
            if entry is not None:
                store.delete_provider(entry.id)
                done.removed = True
            entry = None
        elif entry is None:
            position = max((item.position for item in store.list_providers()), default=-1) + 1
            entry = store.insert_provider(replace(wanted.provider, position=position))
            done.created = True
            # A new entry: every old subject is the older version's, as at the migration.
            touched = set(olds)
        else:
            values = providers.values_of(
                entry,
                label=wanted.provider.label,
                client_id=wanted.provider.client_id,
                client_secret=wanted.provider.client_secret,
                auto_create=wanted.provider.auto_create,
            )
            if not protocol.same_issuer(entry.issuer, wanted.provider.issuer):
                providers.change_issuer(store, entry, wanted.provider.issuer)
                values = replace(values, issuer=wanted.provider.issuer, managed=wanted.provider.managed)
                done.issuer_changed = True
            if not had_provider:
                values = replace(values, enabled=True, trusts_second_factor=trusts_second_factor)
            entry = store.update_provider(entry.id, values)
            done.values_taken = True
    if entry is not None:
        _take_subjects(store, entry, {account_id: olds.get(account_id, "") for account_id in touched}, done)
    if done.wrote:
        store.commit()
        protocol.clear_caches()
    _log().warning(
        "OIDC: took over what the older version changed: removed %s, created %s, issuer changed %s, values %s, "
        "%d linked, %d unlinked, %d left (subject held elsewhere)",
        done.removed,
        done.created,
        done.issuer_changed,
        done.values_taken,
        len(done.linked),
        len(done.unlinked),
        len(done.refused),
    )
    return done

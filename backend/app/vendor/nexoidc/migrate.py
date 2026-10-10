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
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from . import config, coupling, model, protocol, providers
from .model import LEGACY_SLUG, Link, Provider, ProviderValues, Store

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

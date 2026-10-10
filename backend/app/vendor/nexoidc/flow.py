"""The way out to the provider and back (Bauplan 02, "Ablauf"), without a web framework.

``begin`` answers the start route with a redirect to the provider and the attempt cookie; ``finish`` checks the return
and hands out the provider, the verified identity and what the attempt was for; ``refused`` turns any failure into
the redirect to the sign-in page (or the account page for a linking) that deletes the cookie. The app's routes only
translate these plain values into its framework's responses; who the identity is decides ``accounts.py``.

Paths, relative to the app's API (``AppConfig.api_prefix``):

- ``<api>/oidc/<slug>/start`` (``?invite=<key>`` for an invitation), ``<api>/oidc/<slug>/callback``;
- ``<api>/oidc/callback``: the old address of a single-provider app, valid for ever, owned by the entry with the slug
  ``oidc`` (the migrated one, or the nexsuite coupling). A provider registered before only knows this one.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from . import attempt as attempts
from . import config, protocol
from .errors import OidcError
from .model import LEGACY_SLUG, Identity, Provider, Store

#: How much of a provider's error text goes into the log; it comes from outside and has no length limit.
FOREIGN_TEXT_MAX = 200


def _log() -> logging.Logger:
    return logging.getLogger(config.current().log_name)


@dataclass(frozen=True)
class Cookie:
    """The attempt cookie as the app sets it: ``HttpOnly``, ``SameSite=Lax`` (the return from the provider is a
    top-level navigation; ``Strict`` would drop exactly that), ``Secure`` when the app is reached over HTTPS (the app
    knows). An empty ``value`` with ``max_age`` 0 deletes it."""

    name: str
    value: str
    path: str
    max_age: int
    http_only: bool = True
    same_site: str = "lax"
    #: ``Secure`` whenever the app is reached over HTTPS (its public address, else the request).
    secure: bool = False


@dataclass(frozen=True)
class Redirect:
    """Where the browser goes next, and what happens to the attempt cookie."""

    location: str
    cookie: Cookie


@dataclass(frozen=True)
class Arrival:
    """A return that passed every check: the provider, the identity it vouches for, and what the attempt was for."""

    provider: Provider
    identity: Identity
    started: attempts.Started


def callback_path(slug: str, redirect_path: str = "") -> str:
    """The callback of an entry, relative to the app root: its own ``redirect_path`` when it has one (an address a
    provider knew before the module), else ``<api>/oidc/<slug>/callback``; the entry ``oidc`` keeps the old address
    ``<api>/oidc/callback``."""
    if redirect_path:
        return redirect_path
    prefix = config.current().api_prefix
    if slug == LEGACY_SLUG:
        return f"{prefix}/oidc/callback"
    return f"{prefix}/oidc/{slug}/callback"


def cookie_path_for(path: str) -> str:
    """The path of the attempt cookie for a callback path: ``<api>/oidc`` for every callback below it (Bauplan 02),
    else the callback path itself, so that the browser sends the cookie to an old address too."""
    standard = config.current().cookie_path
    return standard if path == standard or path.startswith(standard + "/") else path


def base_address(request_base: str) -> str:
    """The app's public address without a slash at the end: the configured one, else that of the request."""
    configured = config.current().public_url().strip().rstrip("/")
    return configured or request_base.strip().rstrip("/")


def redirect_uri(slug: str, request_base: str, redirect_path: str = "") -> str:
    """The callback address as the provider has to know it."""
    return base_address(request_base) + callback_path(slug, redirect_path)


def provider_redirect_uri(provider: Provider, request_base: str) -> str:
    return redirect_uri(provider.slug, request_base, provider.redirect_path)


def _deleting_cookie(uri: str | None = None) -> Cookie:
    """The cookie gone again, at the path and with the flag it was set with (from the address it was made for)."""
    cfg = config.current()
    if not uri:
        return Cookie(cfg.cookie_name, "", cfg.cookie_path, 0)
    parts = urlsplit(uri)
    return Cookie(cfg.cookie_name, "", cookie_path_for(parts.path), 0, secure=parts.scheme == "https")


def _usable(provider: Provider | None) -> Provider:
    if provider is None or not provider.enabled or not provider.issuer or not provider.client_id:
        raise OidcError("oidc_not_configured", "This provider is not set up.")
    return provider


async def begin(
    store: Store,
    slug: str,
    *,
    request_base: str,
    invite: str | None = None,
    link_account_id: int | None = None,
) -> Redirect:
    """Start an attempt at the provider ``slug``. With ``invite`` the attempt carries the invitation to the provider;
    with ``link_account_id`` it links that (signed-in, password re-entered) account instead of signing in. Raises
    ``OidcError``; the app answers it with ``refused``."""
    cfg = config.current()
    provider = _usable(store.provider_by_slug(slug))
    purpose = attempts.SIGN_IN
    if link_account_id is not None:
        purpose = attempts.LINK
    elif invite is not None:
        if cfg.single_account or not invite or store.find_invite(invite) is None:
            raise OidcError("invite_invalid", "The invitation ran out or has been used.")
        purpose = attempts.INVITE
    description = await protocol.discovery(provider.issuer)
    attempt = protocol.new_attempt()
    uri = provider_redirect_uri(provider, request_base)
    value = attempts.pack(
        attempt,
        provider_id=provider.id,
        purpose=purpose,
        redirect_uri=uri,
        link_account_id=link_account_id,
        invite=invite if purpose == attempts.INVITE else None,
    )
    url = protocol.authorization_url(description, provider, uri, attempt)
    _log().info("OIDC %s started at provider %s", purpose.replace("_", " "), provider.slug)
    cookie = Cookie(
        cfg.cookie_name,
        value,
        cookie_path_for(urlsplit(uri).path),
        attempts.ATTEMPT_MINUTES * 60,
        secure=uri.startswith("https://"),
    )
    return Redirect(url, cookie)


async def finish(store: Store, slug: str, *, cookie: str | None, params: Mapping[str, str]) -> Arrival:
    """Check a return from the provider ``slug`` (``params`` is the query of the callback).

    The order is deliberate: the provider's own error first (such a return carries no code), then the attempt cookie
    and its ``state``, then the single use of the ``state``, and only then the provider is asked: exchange, token,
    userinfo. Nothing is written here."""
    provider = _usable(store.provider_by_slug(slug))
    if params.get("error"):
        reason = f"provider returned error={params['error'][:FOREIGN_TEXT_MAX]!r}"
        if params.get("error_description"):
            reason += f" description={params['error_description'][:FOREIGN_TEXT_MAX]!r}"
        raise OidcError("oidc_provider_error", reason)
    started = attempts.read(cookie)
    if started is None:
        raise OidcError("oidc_state_mismatch", "attempt cookie missing, expired or not ours")
    code, state = params.get("code") or "", params.get("state") or ""
    if not code or not state:
        raise OidcError("oidc_state_mismatch", "callback without code or state")
    if not hmac.compare_digest(state.encode(), started.state.encode()):
        raise OidcError("oidc_state_mismatch", "state does not match the running attempt")
    if started.provider_id != provider.id:
        raise OidcError("oidc_state_mismatch", "the attempt was started at another provider")
    if not attempts.consume_state(state):
        raise OidcError("oidc_state_mismatch", "state was already used")
    description = await protocol.discovery(provider.issuer)
    id_token, access_token = await protocol.exchange_code(
        description, provider, code, started.redirect_uri, started.verifier
    )
    identity = await protocol.verify_id_token(description, provider.client_id, id_token, started.nonce, access_token)
    return Arrival(provider=provider, identity=identity, started=started)


def refused(error: OidcError, cookie: str | None) -> Redirect:
    """The way back after a failure: the sign-in page with the code, or the account page when the attempt was a
    linking. The cookie goes. One line in the log with the reason (never a token, a code, a state or a secret)."""
    cfg = config.current()
    started = attempts.read(cookie)
    linking = started is not None and started.purpose == attempts.LINK
    _log().warning("OIDC %s refused: %s (%s)", "linking" if linking else "sign-in", error.code, error.message)
    page = cfg.account_page if linking else cfg.login_page
    gone = _deleting_cookie(started.redirect_uri if started is not None else None)
    return Redirect(f"{page}?error={quote(error.code)}", gone)


def arrived(arrival: Arrival | None = None, location: str | None = None) -> Redirect:
    """The way on after a successful sign-in (to ``home``) or linking (``location``): the cookie goes, at the path it
    was set for."""
    gone = _deleting_cookie(arrival.started.redirect_uri if arrival is not None else None)
    return Redirect(location or config.current().home, gone)


def linked_page(slug: str) -> str:
    """Where a successful linking ends: the account page, naming the provider."""
    return f"{config.current().account_page}?linked={quote(slug)}"

"""OpenID Connect: discovery, the redirect to the provider, the code exchange and the token checks.

Only the protocol lives here, no accounts and no database. What happens with a verified identity is the
router's business (``routers/oidc.py``); this module delivers the identity.

The flow is the Authorization Code Flow with PKCE:

1. **Outbound:** nextrmnl draws three random values (``state``, ``nonce``, the PKCE ``verifier``), packs them
   into a short-lived signed cookie and sends the browser to the provider.
2. **Return:** the provider sends the browser back with a one-time code. nextrmnl checks ``state`` against the
   cookie, exchanges the code (with the ``verifier``) for the tokens and checks the id token: signature against
   the published keys, issuer, audience, expiry, ``nonce``.

Why all three values although they look alike: ``state`` stops somebody from planting a foreign answer into a
browser (CSRF on the return leg). ``nonce`` stops an intercepted id token from being redeemed a second time,
because it sits inside the token, not in the address. The PKCE ``verifier`` makes an intercepted code
worthless: only whoever knows the preimage of the challenge can redeem it. On top of that every ``state`` is
consumed once on the server, so a replayed callback is refused even with the original cookie.

No OIDC library: httpx fetches the documents, PyJWT checks the signatures. The standard is small enough to
write out, and a library would bring a second HTTP stack and a second JWT interpretation.

``transport_for_tests`` lets the tests inject an ``httpx.MockTransport``; nothing here touches the network then.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from ..config import get_settings

logger = logging.getLogger("nextrmnl.oidc")

#: Signature algorithms nextrmnl accepts: asymmetric ones only. RS256 is what the standard requires and what
#: nearly every provider uses; the others are common variants. HS256 is missing on purpose: a symmetrically
#: signed token would be checked with the client secret, and an attacker who bends the ``alg`` header could
#: sign in with a home-made token. That is the best known JWT attack.
#:
#: What is missing here locks people out: a provider signs with whatever its operator configured. ES512 and
#: EdDSA (Ed25519 only; PyJWT rejects Ed448 keys) are in the list because Pocket ID lets one pick them.
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")

#: Clock tolerance in seconds. Self-hosted providers run on machines whose clock has been known to lag.
CLOCK_LEEWAY = 60
#: How long a started sign-in attempt is valid. Longer than anybody needs to sign in, short enough that a
#: forgotten cookie is worthless.
ATTEMPT_MINUTES = 10
#: Discovery document and keys are cached: they rarely change, and without the cache every sign-in would start
#: with three fetches.
CACHE_SECONDS = 3600
#: The usual limit for calls to the provider: discovery, keys, token exchange. Small self-hosted providers
#: occasionally need a few seconds.
TIMEOUT_SECONDS = 10
#: The userinfo call gets a shorter leash: it hangs on every sign-in and its absence is bearable, the fallback
#: is "no additional information". The token exchange is the opposite; without it there is no sign-in.
USERINFO_SECONDS = 5

COOKIE_NAME = "nextrmnl_oidc"
COOKIE_PATH = "/api/oidc"

#: Tests set an ``httpx.MockTransport`` here. In production it stays None and httpx uses the network.
transport_for_tests: httpx.BaseTransport | None = None


class OidcError(Exception):
    """The sign-in attempt fails, with a code the sign-in page can show."""

    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Identity:
    """What is certain after a successful run.

    ``issuer`` plus ``subject`` is the identity in the sense of the standard; name and address are extras the
    provider may change at any time. ``email_verified`` comes literally from the provider: only when it is true
    may the address serve as a bridge to an existing account.
    """

    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    username: str | None


@dataclass(frozen=True)
class Attempt:
    """The three random values of one sign-in attempt."""

    state: str
    nonce: str
    verifier: str

    @property
    def challenge(self) -> str:
        """The PKCE challenge for the verifier (S256)."""
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_attempt() -> Attempt:
    return Attempt(state=secrets.token_urlsafe(24), nonce=secrets.token_urlsafe(24), verifier=secrets.token_urlsafe(48))


def masked(email: str | None) -> str:
    """An address for the log: recognisable, not complete. The log runs for weeks and travels with bug reports."""
    if not email:
        return "none"
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}" if domain else f"{local[:2]}***"


# ---------------------------------------------------------------------------
# Calls to the provider
# ---------------------------------------------------------------------------

#: issuer -> (document, fetched at). A plain in-process cache is enough: losing it at a restart costs one fetch.
_discovery_cache: dict[str, tuple[dict[str, Any], float]] = {}
_jwks_cache: dict[str, tuple[dict[str, Any], float]] = {}


def _client() -> httpx.AsyncClient:
    # A fresh client per operation instead of a shared one: the app runs under different event loops in the
    # tests, and a sign-in is rare enough that connection reuse buys nothing.
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=transport_for_tests)


def clear_cache() -> None:
    """For the tests, and for the moment the operator changes the issuer."""
    _discovery_cache.clear()
    _jwks_cache.clear()


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


def _as_object(response: httpx.Response, purpose: str, url: str) -> dict[str, Any]:
    """The body as a JSON object, or the real reason why not.

    The most common case is not broken JSON but none at all: a reverse proxy in front of the provider, a typo
    in the path, a portal page. Back comes HTML, often with status 200. That is why the log names the content
    type. The body itself never goes into the log: the token response carries tokens.
    """
    try:
        data = response.json()
    except ValueError as error:
        logger.warning(
            "OIDC: the %s at %r is not JSON (content-type %s, %d bytes)",
            purpose,
            url,
            _content_type(response) or "none",
            len(response.content),
        )
        raise OidcError("oidc_provider_invalid", "The provider's answer is not understandable.") from error
    if not isinstance(data, dict):
        logger.warning("OIDC: the %s at %r is JSON but not an object (%s)", purpose, url, type(data).__name__)
        raise OidcError("oidc_provider_invalid", "The provider's answer is not understandable.")
    return data


async def _fetch_json(url: str, purpose: str) -> dict[str, Any]:
    try:
        async with _client() as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        logger.warning("OIDC: fetching the %s from %r failed: %r", purpose, url, error)
        raise OidcError("oidc_provider_unreachable", "The provider cannot be reached right now.") from error
    except Exception as error:
        # ``httpx.InvalidURL`` inherits from Exception directly, not from HTTPError, and it is raised while
        # parsing the address, before any request exists. The addresses here come from outside (the operator
        # types the issuer, the discovery document names ``jwks_uri``); a typo must come back as a setup error,
        # not as a bare 500.
        logger.warning("OIDC: the address for the %s cannot be used at all (%r): %r", purpose, url, error)
        raise OidcError("oidc_provider_unreachable", "The provider cannot be reached right now.") from error
    if not response.is_success:
        # A redirect counts as failure: the client follows none, and ``location`` usually names the cause
        # (http instead of https, or a portal in front of the provider).
        target = response.headers.get("location")
        logger.warning(
            "OIDC: fetching the %s from %r answered %d (content-type %s%s)",
            purpose,
            url,
            response.status_code,
            _content_type(response) or "none",
            f", redirect to {target!r}" if target else "",
        )
        raise OidcError("oidc_provider_unreachable", "The provider cannot be reached right now.")
    return _as_object(response, purpose, url)


async def discovery(issuer_url: str, *, fresh: bool = False) -> dict[str, Any]:
    """The provider's discovery document, cached.

    The ``issuer`` inside the document must equal the requested address. The standard demands it, and it is no
    formality: the value is later compared character by character with the ``iss`` of every token. A provider
    answering under one address and claiming another would silently defeat that check.
    """
    issuer = issuer_url.rstrip("/")
    if not fresh:
        cached = _discovery_cache.get(issuer)
        if cached is not None and time.monotonic() - cached[1] < CACHE_SECONDS:
            return cached[0]
    data = await _fetch_json(f"{issuer}/.well-known/openid-configuration", "provider description")
    reported = str(data.get("issuer") or "").rstrip("/")
    if reported != issuer:
        logger.warning("OIDC: provider at %r calls itself %r, refusing the mismatch", issuer, reported)
        raise OidcError("oidc_issuer_mismatch", "The provider reports a different address than configured.")
    missing = [key for key in ("authorization_endpoint", "token_endpoint", "jwks_uri") if not data.get(key)]
    if missing:
        logger.warning("OIDC: the provider description at %r is missing %s", issuer, ", ".join(missing))
        raise OidcError("oidc_provider_invalid", "The provider's answer is not understandable.")
    _discovery_cache[issuer] = (data, time.monotonic())
    return data


def _find_kid(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
    keys = [key for key in jwks.get("keys", []) if isinstance(key, dict)]
    if kid is None:
        # Without a ``kid`` the situation is only unambiguous with exactly one signing key. Guessing would be
        # worse than refusing.
        usable = [key for key in keys if key.get("use") in (None, "sig")]
        return usable[0] if len(usable) == 1 else None
    for key in keys:
        if key.get("kid") == kid:
            return key
    return None


async def _signing_key(jwks_uri: str, kid: str | None) -> dict[str, Any]:
    """The signing key for this ``kid``.

    An unknown ``kid`` triggers one fresh fetch: providers rotate their keys, and the cache would not know.
    Only when the fresh set does not know it either is the token really uncheckable.
    """
    cached = _jwks_cache.get(jwks_uri)
    if cached is not None and time.monotonic() - cached[1] < CACHE_SECONDS:
        found = _find_kid(cached[0], kid)
        if found is not None:
            return found
    data = await _fetch_json(jwks_uri, "signing keys")
    _jwks_cache[jwks_uri] = (data, time.monotonic())
    found = _find_kid(data, kid)
    if found is None:
        # Key ids are public, they stand in the same document; naming them tells the operator whether the
        # jwks_uri is wrong, the set is empty or only the kid does not match.
        offered = [str(entry.get("kid")) for entry in data.get("keys", []) if isinstance(entry, dict)]
        if kid is None:
            logger.warning(
                "OIDC: the token names no kid and %r offers %d keys, refusing to guess", jwks_uri, len(offered)
            )
        else:
            logger.warning(
                "OIDC: no signing key for kid %r at %r, the provider offers %s",
                kid,
                jwks_uri,
                ", ".join(offered) or "no keys at all",
            )
        raise OidcError("oidc_token_invalid", "The provider's token could not be checked.")
    return found


# ---------------------------------------------------------------------------
# Outbound: the address at the provider
# ---------------------------------------------------------------------------


def authorization_url(description: dict[str, Any], client_id: str, redirect_uri: str, attempt: Attempt) -> str:
    base = str(description["authorization_endpoint"])
    separator = "&" if "?" in base else "?"
    return base + separator + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            # ``openid`` makes the run OIDC at all; ``email`` and ``profile`` ask for address and name. Nothing more.
            "scope": "openid email profile",
            "state": attempt.state,
            "nonce": attempt.nonce,
            "code_challenge": attempt.challenge,
            "code_challenge_method": "S256",
        }
    )


# ---------------------------------------------------------------------------
# Return: exchange and checks
# ---------------------------------------------------------------------------


def _oauth_error(response: httpx.Response) -> str:
    """Why the provider refused, in one line for the log.

    OAuth 2 (RFC 6749, 5.2) prescribes ``error`` and recommends ``error_description``; they say
    ``invalid_client`` or ``invalid_grant`` instead of "something with 400". No JSON at all means something in
    front of the provider answered, not the provider.
    """
    try:
        data = response.json()
    except ValueError:
        return (
            f"no JSON body (content-type {_content_type(response) or 'none'}, {len(response.content)} bytes), "
            "usually a proxy or an error page in front of the provider"
        )
    if not isinstance(data, dict):
        return "a JSON body that is not an object"
    code = str(data.get("error") or "").strip()
    explanation = str(data.get("error_description") or "").strip()
    if not code and not explanation:
        return f"a JSON body without the error field (it carries: {', '.join(sorted(data)) or 'nothing at all'})"
    if not explanation:
        return (
            f"error={code} (no error_description); invalid_client points at the client id or the secret, "
            "invalid_grant at the code or at a redirect_uri the provider does not have on file"
        )
    # ``!r``: some providers put a stack trace with line breaks into the description, and a log line that
    # breaks into several is lost to the log reader.
    return f"error={code or 'none'} error_description={explanation[:300]!r}"


async def exchange_code(
    description: dict[str, Any], client_id: str, client_secret: str, code: str, redirect_uri: str, verifier: str
) -> tuple[str, str | None]:
    """Exchange the one-time code for the tokens: (id_token, access_token or None).

    Authenticated with HTTP Basic (``client_secret_basic``), the method the standard requires every provider to
    support. The secret arrives here already decrypted and is used for this one call.
    """
    url = str(description["token_endpoint"])
    try:
        async with _client() as client:
            response = await client.post(
                url,
                auth=(client_id, client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
            )
    except httpx.HTTPError as error:
        logger.warning("OIDC: the token exchange at %r could not be sent: %r", url, error)
        raise OidcError("oidc_provider_unreachable", "The provider cannot be reached right now.") from error
    except Exception as error:
        # Same trap as in ``_fetch_json``: ``httpx.InvalidURL`` is not an ``HTTPError``.
        logger.warning("OIDC: the token endpoint address %r cannot be used at all: %r", url, error)
        raise OidcError("oidc_provider_unreachable", "The provider cannot be reached right now.") from error

    if response.status_code != 200:
        # Usually a mistyped client id or secret: a setup error, not an outage. The provider's explanation goes
        # into the log, never into a browser.
        logger.warning(
            "OIDC: the token endpoint at %r refused the exchange with %d: %s",
            url,
            response.status_code,
            _oauth_error(response),
        )
        raise OidcError("oidc_exchange_failed", "The provider did not accept the sign-in.")

    data = _as_object(response, "token response", url)
    id_token = data.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        # Field names, never values: the values are tokens. Only access_token and token_type means the provider
        # did not honour the openid scope.
        logger.warning(
            "OIDC: the token endpoint at %r answered 200 without an id_token (the response carries: %s)",
            url,
            ", ".join(sorted(data)) or "nothing at all",
        )
        raise OidcError("oidc_exchange_failed", "The provider did not accept the sign-in.")
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        logger.info("OIDC: the token endpoint at %r answered without an access_token, userinfo will not be asked", url)
        access_token = None
    return id_token, access_token


async def _verify_token(
    description: dict[str, Any], client_id: str, token: str, *, purpose: str, required: tuple[str, ...]
) -> dict[str, Any]:
    """Check and open a signed document of the provider.

    Signature against the published keys, issuer, audience, expiry: the same check for the id token and for a
    signed userinfo answer. A second, milder version would be the hole: whoever draws claims from a signed
    document must demand the same of it as of the id token.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        logger.warning("OIDC: %s is not a readable JWT: %s", purpose, error)
        raise OidcError("oidc_token_invalid", "The provider's token could not be checked.") from error

    jwks_uri = str(description["jwks_uri"])
    jwk = await _signing_key(jwks_uri, header.get("kid"))
    try:
        key = jwt.PyJWK(jwk).key
    except jwt.PyJWTError as error:
        logger.warning(
            "OIDC: unusable signing key from %r (kid %r, kty %r, crv %r): %s",
            jwks_uri,
            jwk.get("kid"),
            jwk.get("kty"),
            jwk.get("crv"),
            error,
        )
        raise OidcError("oidc_token_invalid", "The provider's token could not be checked.") from error

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(ALGORITHMS),
            audience=client_id,
            issuer=str(description["issuer"]),
            leeway=CLOCK_LEEWAY,
            options={"require": list(required)},
        )
    except jwt.PyJWTError as error:
        # ``alg`` belongs in the line: an algorithm outside ALGORITHMS looks like a bad signature otherwise.
        logger.warning(
            "OIDC: %s was rejected (alg %r, kid %r, expected issuer %r): %s",
            purpose,
            header.get("alg"),
            header.get("kid"),
            description.get("issuer"),
            error,
        )
        raise OidcError("oidc_token_invalid", "The provider's token could not be checked.") from error

    # ``azp`` must be this client whenever it is present (OIDC Core 3.1.3.7). ``jwt.decode`` only checks that
    # our client id occurs in ``aud``; a token the provider issued for another application that merely mentions
    # nextrmnl would pass otherwise.
    if "azp" in claims and claims.get("azp") != client_id:
        logger.warning("OIDC: %s carries azp %r, which is not this client", purpose, claims.get("azp"))
        raise OidcError("oidc_token_invalid", "The provider's token could not be checked.")
    return claims


_JWT_PART = re.compile(r"[A-Za-z0-9_-]+")


def _looks_signed(response: httpx.Response) -> bool:
    """Is the body a JWT instead of a JSON object? ``application/jwt`` decides (Core 5.3.2); the look at the
    body is the fallback for a proxy that bends headers."""
    if _content_type(response) == "application/jwt":
        return True
    parts = response.text.strip().split(".")
    return len(parts) == 3 and all(_JWT_PART.fullmatch(part) for part in parts)


async def _read_userinfo(
    description: dict[str, Any], client_id: str, response: httpx.Response, url: str
) -> dict[str, Any] | None:
    """The userinfo answer, JSON or a signed JWT (Authelia and Zitadel can sign it). A signed answer goes
    through the same check as the id token; what fails the check is discarded, not taken unchecked."""
    if _looks_signed(response):
        try:
            # Without ``exp``: the standard requires it for the id token, not for a signed userinfo answer.
            return await _verify_token(
                description,
                client_id,
                response.text.strip(),
                purpose="the signed userinfo",
                required=("iss", "aud", "sub"),
            )
        except OidcError:
            return None
    try:
        data = response.json()
    except ValueError:
        logger.warning(
            "OIDC: userinfo at %r answered neither JSON nor a signed token (content-type %s, %d bytes)",
            url,
            _content_type(response) or "none",
            len(response.content),
        )
        return None
    if not isinstance(data, dict):
        logger.warning("OIDC: userinfo at %r did not answer with an object but with %s", url, type(data).__name__)
        return None
    return data


async def _ask_userinfo(description: dict[str, Any], client_id: str, access_token: str, subject: str) -> dict[str, Any]:
    """Ask the provider what it says about this person (``userinfo``).

    Per OIDC Core only ``sub`` is guaranteed inside the id token; Authelia and Zitadel keep the address here by
    default. A failure must break nothing: the fallback is "no additional information". The answer is discarded
    when its ``sub`` differs from the id token (Core 5.3.2); otherwise a foreign address could be attached to a
    verified sign-in.
    """
    url = description.get("userinfo_endpoint")
    if not isinstance(url, str) or not url:
        logger.info("OIDC: the provider description names no userinfo endpoint, not asking")
        return {}
    try:
        async with _client() as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(url, headers=headers, timeout=USERINFO_SECONDS)
        response.raise_for_status()
        data = await _read_userinfo(description, client_id, response, url)
    except Exception as error:  # noqa: BLE001
        # Every exception on purpose: whoever gets in today without this call must get in tomorrow too, and a
        # catcher that lists exception names ages with the library. ``%r`` names the type.
        logger.warning("OIDC: userinfo at %r could not be read: %r", url, error)
        return {}
    if data is None:
        return {}
    if str(data.get("sub") or "") != subject:
        logger.warning("OIDC: userinfo at %r answered for a different subject, discarded", url)
        return {}
    return data


async def verify_id_token(
    description: dict[str, Any], client_id: str, id_token: str, nonce: str, access_token: str | None = None
) -> Identity:
    """Check the id token and hand out the identity.

    Everything the standard demands: signature against the published keys, issuer, audience, expiry, and the
    ``nonce`` from the outbound leg, so that an intercepted token cannot be redeemed twice.
    """
    claims = await _verify_token(
        description, client_id, id_token, purpose="the id_token", required=("exp", "iss", "aud", "sub")
    )
    if claims.get("nonce") != nonce:
        # A missing nonce means the provider does not mirror it; a different one means the token comes from
        # another run. The values themselves do not belong in the log.
        logger.warning(
            "OIDC: the id_token does not belong to this sign-in, it carries %s",
            "no nonce at all" if claims.get("nonce") is None else "a different nonce",
        )
        raise OidcError("oidc_token_invalid", "The provider's token could not be checked.")

    subject = str(claims["sub"]).strip()
    if not subject:
        # ``require`` only insists that the claim exists. An empty subject would later match every account that
        # has none, so it is refused here, whatever else the token says.
        logger.warning("OIDC: the id_token carries an empty subject")
        raise OidcError("oidc_token_invalid", "The provider's token could not be checked.")
    # Always ask userinfo, not only when the address is missing: a provider may send ``email`` in the token and
    # keep ``email_verified`` only here. The signed token keeps the last word, so ``claims`` sits on the right.
    token_claims = dict(claims)
    extra: dict[str, Any] = {}
    if access_token:
        extra = await _ask_userinfo(description, client_id, access_token, subject)
        claims = {**extra, **claims}

    email = str(claims.get("email") or "").strip().lower() or None
    # Some providers send the flag as a string.
    raw_verified = claims.get("email_verified", False)
    verified = raw_verified is True or str(raw_verified).strip().lower() == "true"
    # The confirmation must belong to this address: ``email_verified`` may come from userinfo while the token
    # carries a different address. Then nobody vouched for the token's address.
    userinfo_email = str(extra.get("email") or "").strip().lower() or None
    if verified and email and userinfo_email and userinfo_email != email and "email_verified" not in token_claims:
        logger.warning(
            "OIDC: the provider vouched for %s at userinfo but the id_token carries %s, address treated as unconfirmed",
            masked(userinfo_email),
            masked(email),
        )
        verified = False

    name = str(claims.get("preferred_username") or claims.get("name") or "").strip()
    if not name and email:
        name = email.split("@", 1)[0]
    return Identity(
        issuer=str(claims["iss"]).rstrip("/"),
        subject=subject,
        email=email,
        email_verified=bool(email) and verified,
        username=name or None,
    )


# ---------------------------------------------------------------------------
# The attempt cookie and the single use of a state
# ---------------------------------------------------------------------------


def _cookie_key() -> bytes:
    # Own signing key, derived from the server secret with its own prefix, so that an attempt cookie never
    # passes as anything else. HS256 here signs nextrmnl's own cookie; provider tokens are never accepted with it.
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return hashlib.sha256(b"nextrmnl-oidc-attempt:" + secret).digest()


def pack_attempt(attempt: Attempt, link_account_id: int | None = None) -> str:
    """The attempt as a signed, short-lived cookie value.

    The state lives with the browser instead of in a table: nothing to clean up, and an unredeemed value is
    worthless after ten minutes. Nobody can forge it without the key; the browser's owner could read it, so
    only values that belong to that browser anyway are inside. ``link_account_id`` marks an attempt that
    links the provider identity to an account that is signed in already, instead of signing somebody in.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "state": attempt.state,
        "nonce": attempt.nonce,
        "verifier": attempt.verifier,
        "iat": now,
        "exp": now + ATTEMPT_MINUTES * 60,
    }
    if link_account_id is not None:
        payload["link"] = link_account_id
    return jwt.encode(payload, _cookie_key(), algorithm="HS256")


def read_attempt(value: str | None) -> dict[str, Any] | None:
    """The attempt from the cookie; None if it is missing, expired or not ours."""
    if not value:
        return None
    try:
        return jwt.decode(value, _cookie_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


#: Hashes of the states already redeemed, with the moment they may be forgotten. A state is bound to one
#: cookie, but a cookie can be replayed; this makes every state single-use on the server as well.
_used_states: dict[str, float] = {}
_used_lock = threading.Lock()


def consume_state(state: str) -> bool:
    """Marks the state as used. False if it was used before."""
    now = time.monotonic()
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    with _used_lock:
        for key in [key for key, until in _used_states.items() if until <= now]:
            del _used_states[key]
        if digest in _used_states:
            return False
        _used_states[digest] = now + ATTEMPT_MINUTES * 60
        return True


def forget_used_states() -> None:
    """For the tests."""
    with _used_lock:
        _used_states.clear()

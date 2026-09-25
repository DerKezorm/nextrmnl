"""Sign-in through an OpenID Connect provider, its configuration, and the authentik button.

The return leg is a browser redirect, not an API answer: the provider sends the browser back with GET, and a
human sees whatever comes out. So every outcome of the callback, every failure included, ends in a redirect
to the sign-in page with a code in the address (``/login?error=<code>``), never in bare JSON. Success ends on
``/`` with the ordinary session cookie.

Accounts: an account with this ``oidc_subject`` signs in. Otherwise an account with the same **verified**
address and no subject yet is linked (the bridge for operators who invited people by hand before turning on
OIDC). Otherwise a member account is created, but only when the operator allows that (``oidc_auto_create``,
off by default: the operator decides in nextrmnl who gets in, not only the provider). Nothing of that happens
with an unverified address: a provider that does not vouch for the address must not decide who owns which
account. The vault of a new OIDC account stays unset until the person chooses a vault password.

A signed-in person can also link their own account to the provider (``/start?link=1``): the attempt cookie
carries the account, and the callback stores the subject on that account instead of signing anybody in.

``/state``, ``/start`` and ``/callback`` are public: they run before there is a session. The operator never
gets locked out by OIDC, because the password sign-in stays open for the operator (``routers/auth.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import crypto
from ..deps import CurrentAccount, DbSession, OperatorAccount, client_ip
from ..meldungen import fehler
from ..models import SIGN_IN_OIDC, Account
from ..security import SESSION_COOKIE, brake, session_account, start_session
from ..services import accounts, authentik, logs, oidc, settings_service
from .auth import _secure, _set_cookie

router = APIRouter(prefix="/api/oidc", tags=["oidc"])
logger = logging.getLogger("nextrmnl.oidc")

LOGIN_PAGE = "/login"
HOME = "/"
#: Where a linking attempt ends, with ``linked=1`` or ``error=<code>`` in the address.
ACCOUNT_PAGE = "/settings?tab=account"
DEFAULT_PROVIDER_NAME = "OpenID Connect"
#: How much of a provider's error text goes into the log; it comes from outside and has no length limit.
FOREIGN_TEXT_MAX = 200


class ConfigIn(BaseModel):
    issuer: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    #: Empty keeps the stored secret; the page never shows it, so an untouched field must not delete it.
    client_secret: str = Field(default="", max_length=500)
    provider_name: str = Field(default="", max_length=64)


class AuthentikSetupIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    token: str = Field(min_length=1, max_length=2000)


@router.get("/config", summary="The OIDC configuration (never the secret)")
def read_config(operator: OperatorAccount, request: Request, db: DbSession) -> dict[str, Any]:
    return _config_view(db, request)


@router.put("/config", summary="Set the OIDC provider; the issuer is checked once")
async def write_config(payload: ConfigIn, operator: OperatorAccount, request: Request, db: DbSession) -> dict[str, Any]:
    issuer = payload.issuer.strip().rstrip("/")
    if not issuer.lower().startswith(("http://", "https://")):
        raise fehler("issuer_invalid", "The issuer must start with http:// or https://.", 422)
    secret = payload.client_secret.strip()
    stored_secret = str(settings_service.get(db, "oidc_client_secret_enc") or "")
    if not secret and not crypto.decrypt_secret(stored_secret):
        raise fehler("secret_required", "Enter the client secret.", 422)
    try:
        await oidc.discovery(issuer, fresh=True)
    except oidc.OidcError as error:
        code = "issuer_unreachable" if error.code == "oidc_provider_unreachable" else "issuer_invalid"
        raise fehler(code, error.message, 422, reason=error.code) from error
    previous = str(settings_service.get(db, "oidc_issuer") or "")
    if previous and previous != issuer:
        # Subjects are stored without the issuer; accounts keep theirs. A different provider may hand out
        # different subjects for the same people, and then the verified address is the only bridge left.
        logger.warning("OIDC issuer changed from %r to %r, existing accounts keep their subjects", previous, issuer)
    values = {
        "oidc_issuer": issuer,
        "oidc_client_id": payload.client_id.strip(),
        "oidc_provider_name": payload.provider_name.strip(),
    }
    if secret:
        values["oidc_client_secret_enc"] = crypto.encrypt_secret(secret)
    settings_service.save(db, values)
    oidc.clear_cache()
    logger.info("OIDC configured issuer=%s by=%s", issuer, operator.name)
    return _config_view(db, request)


@router.delete("/config", status_code=204, summary="Remove the OIDC configuration")
def delete_config(operator: OperatorAccount, db: DbSession) -> None:
    settings_service.save(
        db, {"oidc_issuer": "", "oidc_client_id": "", "oidc_client_secret_enc": "", "oidc_provider_name": ""}
    )
    oidc.clear_cache()
    logger.info("OIDC configuration removed by=%s", operator.name)


@router.get("/state", summary="Is OIDC sign-in available? (no sign-in needed)")
def state(db: DbSession) -> dict[str, Any]:
    # Public: the sign-in page asks before anybody is signed in. It reveals only whether the button exists.
    return {"enabled": _configured(db), "provider_name": _provider_name(db)}


@router.get("/start", summary="Send the browser to the provider (no sign-in needed)")
async def start(request: Request, db: DbSession, link: bool = False) -> RedirectResponse:
    # Public: this is the sign-in button. Failures land on the sign-in page with a code; a JSON error here
    # would be seen only by the person least able to do anything with it.
    link_account_id: int | None = None
    if link:
        # Linking needs somebody signed in; the account is remembered in the attempt cookie, and the callback
        # checks that the same session is still there.
        current = session_account(db, request.cookies.get(SESSION_COOKIE))
        if current is None:
            return _to_login("not_signed_in")
        link_account_id = current.id
    if not _configured(db):
        return _to_account("oidc_not_configured") if link else _to_login("oidc_not_configured")
    try:
        description = await oidc.discovery(str(settings_service.get(db, "oidc_issuer")))
    except oidc.OidcError as error:
        logger.warning("OIDC sign-in could not be started: %s", error.code)
        return _to_account(error.code) if link else _to_login(error.code)
    attempt = oidc.new_attempt()
    client_id = str(settings_service.get(db, "oidc_client_id"))
    url = oidc.authorization_url(description, client_id, _redirect_uri(db, request), attempt)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        oidc.COOKIE_NAME,
        oidc.pack_attempt(attempt, link_account_id),
        max_age=oidc.ATTEMPT_MINUTES * 60,
        path=oidc.COOKIE_PATH,
        httponly=True,
        # ``lax`` lets the cookie travel on the return from the provider, a top-level navigation; ``strict``
        # would break exactly that step. ``secure`` as for the session cookie: on HTTPS nobody may plant an
        # attempt cookie over a clear-text request.
        samesite="lax",
        secure=_secure(request),
    )
    return response


@router.get("/callback", summary="The return from the provider (no sign-in needed)")
async def callback(
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    # Public by nature: the provider sends the browser here. The order of the checks is deliberate: first our
    # own state (cookie, state), then the brake, then the provider, then the account. Nothing is written before
    # everything in front of it has passed, and every exit leaves a line in the log.
    attempt = oidc.read_attempt(request.cookies.get(oidc.COOKIE_NAME))
    linking = attempt is not None and attempt.get("link") is not None

    def refuse(code_out: str, reason: str, *, real: bool = True) -> RedirectResponse:
        # Everything before the brake can be produced without any provider, the callback address is public;
        # on WARNING a stranger could fill the operator's log. What fails after it had a real run behind it.
        line = "OIDC callback refused (%s): code=%s"
        if real:
            logger.warning(line, reason, code_out)
        else:
            logger.info(line, reason, code_out)
        return _to_account(code_out) if linking else _to_login(code_out)

    if error:
        # Checked before the state: a return with ``error`` carries no code and not necessarily a usable state.
        # ``!r`` keeps a foreign text with line breaks on one log line.
        reason = f"provider returned error={error[:FOREIGN_TEXT_MAX]!r}"
        if error_description:
            reason += f" description={error_description[:FOREIGN_TEXT_MAX]!r}"
        return refuse("oidc_denied", reason, real=False)
    if attempt is None:
        return refuse("oidc_state_mismatch", "attempt cookie missing or expired", real=False)
    if not code or not state:
        return refuse("oidc_state_mismatch", "callback without code or state", real=False)
    if attempt.get("state") != state:
        return refuse("oidc_state_mismatch", "state does not match the running attempt", real=False)
    if not oidc.consume_state(state):
        return refuse("oidc_state_mismatch", "state was already used", real=False)

    # The brake counts per sender and only from here on: what fails before this never saw the provider and
    # can be produced for free. There is no secret to guess at this address, so the brake protects the
    # provider from being hammered, nothing more; it must never key on something shared by everybody.
    key = "oidc:" + client_ip(request)
    if brake.wait_seconds(key):
        return refuse("too_many_attempts", "sender is braked", real=False)

    if not _configured(db):
        return refuse("oidc_not_configured", "OIDC was switched off while the browser was at the provider")
    issuer = str(settings_service.get(db, "oidc_issuer"))
    client_id = str(settings_service.get(db, "oidc_client_id"))
    # Decrypted outside the try: ``decrypt_secret`` does not raise, it returns "" for a foreign secret.key, and
    # that must not look like an unreachable provider in the log.
    client_secret = crypto.decrypt_secret(str(settings_service.get(db, "oidc_client_secret_enc")))
    try:
        description = await oidc.discovery(issuer)
        id_token, access_token = await oidc.exchange_code(
            description, client_id, client_secret, code, _redirect_uri(db, request), str(attempt.get("verifier", ""))
        )
        nonce = str(attempt.get("nonce", ""))
        identity = await oidc.verify_id_token(description, client_id, id_token, nonce, access_token)
    except oidc.OidcError as failure:
        brake.failed(key)
        return refuse(failure.code, f"the run at the provider failed: {failure.code}")
    brake.succeeded(key)

    # From here on the provider vouches for the identity; the rest are account questions.
    if linking:
        return _finish_link(db, request, attempt, identity, refuse)
    account = _resolve(db, identity, bool(settings_service.get(db, "oidc_auto_create")))
    if isinstance(account, str):
        return refuse(account, f"no account for this identity: {account} address={oidc.masked(identity.email)}")

    response = RedirectResponse(HOME, status_code=303)
    _delete_attempt_cookie(response)
    token = start_session(db, account, client_ip(request), request.headers.get("user-agent", ""))
    _set_cookie(response, request, token)
    logs.set_actor(account.name)
    logger.info("Signed in via OIDC name=%s", account.name)
    return response


@router.post("/authentik/setup", summary="Set up provider and application in authentik with a one-time token")
async def authentik_setup(
    payload: AuthentikSetupIn, operator: OperatorAccount, request: Request, db: DbSession
) -> dict[str, Any]:
    url = payload.url.strip().rstrip("/")
    if not url.lower().startswith(("http://", "https://")):
        raise fehler("url_invalid", "The authentik address must start with http:// or https://.", 422)
    logger.info("authentik setup started url=%s by=%s", url, operator.name)
    result = await authentik.setup(db, url, payload.token.strip(), _redirect_uri(db, request))
    return result.as_dict()


@router.get("/authentik/blueprint", summary="Download a blueprint that creates the same objects in authentik")
def authentik_blueprint(operator: OperatorAccount, request: Request, db: DbSession) -> Response:
    return Response(
        content=authentik.blueprint(_redirect_uri(db, request)),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="nextrmnl-authentik.yaml"'},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configured(db: DbSession) -> bool:
    return bool(
        settings_service.get(db, "oidc_issuer")
        and settings_service.get(db, "oidc_client_id")
        and settings_service.get(db, "oidc_client_secret_enc")
    )


def _provider_name(db: DbSession) -> str:
    return str(settings_service.get(db, "oidc_provider_name") or "") or DEFAULT_PROVIDER_NAME


def _redirect_uri(db: DbSession, request: Request) -> str:
    """The callback address as registered at the provider: from the public address if set (the setting, else
    ``NEXTRMNL_PUBLIC_URL``), else from the request. Behind a proxy the public address is the only one the
    provider knows."""
    base = settings_service.public_url(db) or str(request.base_url).rstrip("/")
    return f"{base}/api/oidc/callback"


def _config_view(db: DbSession, request: Request) -> dict[str, Any]:
    return {
        "configured": _configured(db),
        "issuer": str(settings_service.get(db, "oidc_issuer") or ""),
        "client_id": str(settings_service.get(db, "oidc_client_id") or ""),
        "provider_name": str(settings_service.get(db, "oidc_provider_name") or ""),
        "redirect_uri": _redirect_uri(db, request),
    }


def _to_account(code: str) -> RedirectResponse:
    """Back to the account page after a failed linking; the person is signed in, so the sign-in page is wrong."""
    response = RedirectResponse(f"{ACCOUNT_PAGE}&error={code}", status_code=303)
    _delete_attempt_cookie(response)
    return response


def _to_login(code: str) -> RedirectResponse:
    # 303: the browser loads the target with GET whatever way it came.
    response = RedirectResponse(f"{LOGIN_PAGE}?error={code}", status_code=303)
    _delete_attempt_cookie(response)
    return response


def _delete_attempt_cookie(response: Response) -> None:
    response.delete_cookie(oidc.COOKIE_NAME, path=oidc.COOKIE_PATH)


def _finish_link(
    db: DbSession, request: Request, attempt: dict[str, Any], identity: oidc.Identity, refuse: Any
) -> RedirectResponse:
    """Stores the provider identity on the account that started the linking. Nobody is signed in here."""
    current = session_account(db, request.cookies.get(SESSION_COOKIE))
    if current is None or current.id != attempt.get("link"):
        return refuse("oidc_link_mismatch", "the linking attempt does not belong to the signed-in account")
    other = db.scalar(select(Account).where(Account.oidc_subject == identity.subject, Account.id != current.id))
    if other is not None:
        return refuse("oidc_subject_taken", f"this identity already belongs to account {other.name!r}")
    current.oidc_subject = identity.subject
    if identity.email and identity.email_verified and not current.email:
        current.email = identity.email
    db.commit()
    logs.set_actor(current.name)
    logger.info("Account %s linked to its OIDC identity by its owner", current.name)
    response = RedirectResponse(f"{ACCOUNT_PAGE}&linked=1", status_code=303)
    _delete_attempt_cookie(response)
    return response


@router.delete("/link", status_code=204, summary="Forget the link between the own account and the provider")
def unlink(account: CurrentAccount, db: DbSession) -> None:
    if account.sign_in == SIGN_IN_OIDC:
        raise fehler("oidc_only_account", "This account has no password; it signs in through the provider only.", 409)
    account.oidc_subject = ""
    db.commit()
    logger.info("Account %s unlinked from its OIDC identity", account.name)


def _resolve(db: DbSession, identity: oidc.Identity, auto_create: bool) -> Account | str:
    """The account for this identity, or the code of the refusal.

    1. An account with this subject: the usual case, whatever the address says today.
    2. A verified address that belongs to an account without a subject: link it. An account that already has
       a different subject is not taken over; two people at the provider may not share one vault.
    3. Otherwise a new member account, but only with a verified address and only if the operator allows it.
    """
    existing = db.scalar(select(Account).where(Account.oidc_subject == identity.subject))
    if existing is not None:
        return existing
    if not identity.email or not identity.email_verified:
        # The provider does not vouch for the address (authentik's default since 2025.10, also Keycloak and
        # Pocket ID). Without it neither the bridge nor a new account is safe: whoever registers a foreign
        # address at some provider would otherwise take over the account behind it.
        logger.warning(
            "OIDC sign-in refused: the provider did not confirm the address (address=%s, verified=%s)",
            oidc.masked(identity.email),
            identity.email_verified,
        )
        return "oidc_email_unverified"
    by_email = db.scalar(select(Account).where(func.lower(Account.email) == identity.email))
    if by_email is not None:
        if by_email.oidc_subject:
            logger.warning(
                "OIDC sign-in refused: the address %s belongs to account %r with a different subject",
                oidc.masked(identity.email),
                by_email.name,
            )
            return "oidc_email_taken"
        by_email.oidc_subject = identity.subject
        db.commit()
        logger.info("Account %s linked to its OIDC identity by verified address", by_email.name)
        return by_email
    if not auto_create:
        return "oidc_no_account"
    name = identity.username or identity.email.split("@", 1)[0]
    return accounts.create_oidc(db, name, identity.subject, identity.email)

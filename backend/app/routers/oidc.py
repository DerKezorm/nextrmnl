"""Sign-in through OpenID Connect providers, their list, linking, and the authentik button.

The protocol, who an identity is, the provider list and the button live in the shared module ``vendor/nexoidc`` (the
blueprint "Anmeldung mit OIDC und authentik", the same in every nex app); this router puts nextrmnl around it:
sessions, the sign-in brake, the password asked again before linking, the second factor, the direct link of 0.4.1,
and the error answers.

The return leg is a browser redirect, not an API answer: the provider sends the browser back with GET, and a human
sees whatever comes out. So every outcome of a callback ends in a redirect: to ``/`` (or the page the sign-in started
from, see below) with the session cookie, to the sign-in page with ``?error=<code>``, to ``/login?step=code`` when the
code of nextrmnl's own second factor is still due, or to the account page after linking.

A direct link survives the way through the provider: the sign-in button sends the page it was pressed on
(``/api/oidc/<slug>/start?next=/connect/12``), it travels in a signed cookie of its own next to the module's attempt
cookie, and only nextrmnl's own pages are taken (``safe_next``, checked on the way out and again on the way back).

Paths (all below ``/api/oidc``):

| Method | Path | Who |
|---|---|---|
| GET | ``/providers`` | anybody: slug and label of the active entries (sign-in and invitation page) |
| GET, POST | ``/admin/providers`` | operator: the list (never a secret), add one |
| PUT | ``/admin/providers/order`` | operator: ``{"ids": [...]}`` |
| PUT, DELETE | ``/admin/providers/{id}`` | operator: save (``dropped`` links), remove (``count``, ``only``) |
| GET | ``/admin/providers/{id}/impact?issuer=`` | operator: the numbers for the two confirmations |
| GET | ``/{slug}/start[?invite=<token>][&next=<page>]`` | anybody: off to the provider |
| GET | ``/{slug}/callback``, ``/callback`` | anybody: the return (the second belongs to the entry ``oidc``) |
| GET | ``/me`` | signed in: every active entry with ``linked`` |
| POST, DELETE | ``/{slug}/link`` | signed in: link (``{"password"}`` gives ``{"url"}``), unlink |
| DELETE | ``/admin/accounts/{account}/links/{provider}`` | operator, with the own password |
| POST | ``/authentik/setup`` | operator: ``{"url", "token"}`` gives always 200 with the steps |
| GET | ``/authentik/blueprint`` | operator: ``nextrmnl-authentik.yaml`` |
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import SessionLocal
from ..deps import CurrentAccount, OperatorAccount, client_ip, reauth_failed, reauth_guard, reauth_succeeded
from ..meldungen import fehler
from ..models import SIGN_IN_PASSWORD, Account
from ..security import SESSION_COOKIE, brake, session_account, start_session
from ..services import accounts, logs, oidc_store, totp
from ..services.oidc_store import SqlStore
from ..vendor.nexoidc import OidcError, ProviderInvalid, authentik, flow, providers
from ..vendor.nexoidc import accounts as oidc_accounts
from ..vendor.nexoidc.config import current
from .auth import _secure, _set_cookie, _set_pending_cookie, end_ssh_sessions

router = APIRouter(prefix="/api/oidc", tags=["oidc"])
logger = logging.getLogger("nextrmnl.oidc")

#: Where the sign-in page asks for the code of nextrmnl's own second factor after a provider that is not trusted.
CODE_STEP = "/login?step=code"
#: Codes that cost nothing to produce (a forged return, a braked sender): they do not count against the sender.
FREE_REFUSALS = ("oidc_state_mismatch", "too_many_attempts", "oidc_not_configured")
#: The page a sign-in started from, next to the module's attempt cookie (same path, same lifetime).
NEXT_COOKIE = "nextrmnl_oidc_next"
NEXT_MINUTES = 10
#: Where a sign-in may land after the provider: the app's own pages and a direct link to one connection. A fixed
#: list and a full match, nothing assembled from the input. Anything else (another host, ``//host``, a backslash,
#: an encoded slash, a query, a path that climbs) is no target, and the sign-in ends on the start page.
_NEXT = re.compile(r"/(?:sessions|vault|settings|about|connect/[1-9][0-9]{0,9})?")


class ProviderIn(BaseModel):
    label: str = Field(max_length=200)
    slug: str = Field(default="", max_length=100)
    issuer: str = Field(max_length=600)
    client_id: str = Field(max_length=300)
    client_secret: str = Field(default="", max_length=2000)
    scopes: str = Field(default="openid profile email", max_length=500)
    enabled: bool = True
    auto_create: bool = False
    trusts_second_factor: bool = True


class PasswordIn(BaseModel):
    password: str = Field(max_length=200)


class SetupIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    token: str = Field(min_length=1, max_length=2000)
    #: The operator said yes to the question after ``reason: issuer_change``.
    confirm_issuer_change: bool = False


class OrderIn(BaseModel):
    ids: list[int] = Field(max_length=200)


@contextmanager
def store() -> Iterator[SqlStore]:
    with SessionLocal() as db:
        yield SqlStore(db)


def _base(request: Request) -> str:
    return str(request.base_url)


def _redirect(target: flow.Redirect, request: Request) -> RedirectResponse:
    # 303: the browser loads the target with GET whatever way it came.
    response = RedirectResponse(target.location, status_code=303)
    cookie = target.cookie
    secure = cookie.secure or _secure(request)
    if cookie.max_age == 0:
        response.delete_cookie(cookie.name, path=cookie.path, secure=secure, httponly=True, samesite="lax")
    else:
        response.set_cookie(
            cookie.name,
            cookie.value,
            max_age=cookie.max_age,
            path=cookie.path,
            httponly=cookie.http_only,
            samesite="lax",
            secure=secure,
        )
    return response


def _form_error(exc: ProviderInvalid) -> Exception:
    return fehler(exc.code, exc.message, 422, field=exc.field)


# --- The direct link (0.4.1) ---------------------------------------------------------------------------------------


def safe_next(value: object) -> str | None:
    """The page to land on after the provider, or None if ``value`` is not one of nextrmnl's own."""
    if isinstance(value, str) and _NEXT.fullmatch(value):
        return value
    return None


def _next_key() -> bytes:
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return hashlib.sha256(b"nextrmnl-oidc-next:" + secret).digest()


def _pack_next(page: str) -> str:
    until = str(int(time.time()) + NEXT_MINUTES * 60)
    body = base64.urlsafe_b64encode(f"{until}|{page}".encode()).decode().rstrip("=")
    mac = hmac.new(_next_key(), body.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{body}.{mac}"


def _read_next(value: str | None) -> str | None:
    """The page from the cookie: signed by this server, not run out, and one of nextrmnl's own; else None."""
    if not value or "." not in value:
        return None
    body, mac = value.rsplit(".", 1)
    if not hmac.compare_digest(hmac.new(_next_key(), body.encode(), hashlib.sha256).hexdigest()[:40], mac):
        return None
    try:
        until, page = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().split("|", 1)
        if int(until) < time.time():
            return None
    except ValueError:
        return None
    return safe_next(page)


def _drop_next(response: Response, request: Request) -> None:
    response.delete_cookie(NEXT_COOKIE, path=current().cookie_path, secure=_secure(request), httponly=True,
                           samesite="lax")


# --- The provider list ---------------------------------------------------------------------------------------------


@router.get("/providers", summary="The active sign-in providers: slug and label (no sign-in needed)")
def public_providers() -> list[dict[str, str]]:
    # Public: the sign-in and invitation pages ask before anybody is signed in. Nothing but the buttons leaves.
    with store() as s:
        return providers.public_list(s)


@router.get("/admin/providers", summary="The provider list for the operator (never a secret)")
def admin_providers(_operator: OperatorAccount, request: Request) -> list[dict[str, Any]]:
    with store() as s:
        coupled = providers.coupled(s)
        return [
            providers.admin_view(entry, _base(request), len(s.links_of_provider(entry.id)), coupled=coupled)
            for entry in s.list_providers()
        ]


@router.post("/admin/providers", status_code=201, summary="Add a provider; the issuer is checked by discovery")
async def add_provider(payload: ProviderIn, operator: OperatorAccount, request: Request) -> dict[str, Any]:
    with store() as s:
        try:
            created = await providers.create(s, providers.Form(**payload.model_dump()))
        except ProviderInvalid as exc:
            raise _form_error(exc) from exc
        logger.info("OIDC provider %s added by=%s", created.slug, operator.name)
        return providers.admin_view(created, _base(request), 0)


@router.put("/admin/providers/order", summary="Put the providers in this order (the drag handle)")
def order_providers(payload: OrderIn, _operator: OperatorAccount) -> list[dict[str, str]]:
    with store() as s:
        try:
            providers.reorder(s, payload.ids)
        except ProviderInvalid as exc:
            raise _form_error(exc) from exc
        return providers.public_list(s)


@router.put("/admin/providers/{provider_id}", summary="Save a provider; another issuer drops its links")
async def save_provider(
    provider_id: Annotated[int, Path(ge=1, le=2**31)], payload: ProviderIn, operator: OperatorAccount, request: Request
) -> dict[str, Any]:
    with store() as s:
        try:
            saved, dropped = await providers.update(s, provider_id, providers.Form(**payload.model_dump()))
        except ProviderInvalid as exc:
            raise _form_error(exc) from exc
        logger.info("OIDC provider %s saved by=%s", saved.slug, operator.name)
        view = providers.admin_view(saved, _base(request), len(s.links_of_provider(saved.id)))
        return {**view, "dropped": dropped}


@router.get("/admin/providers/{provider_id}/impact", summary="Who loses a link when the issuer changes or it goes")
def provider_impact(
    provider_id: Annotated[int, Path(ge=1, le=2**31)],
    _operator: OperatorAccount,
    issuer: Annotated[str, Query(max_length=600)] = "",
) -> dict[str, Any]:
    with store() as s:
        if s.get_provider(provider_id) is None:
            raise fehler("provider_unknown", "No such provider.", 404, field="id")
        removal = providers.removal_impact(s, provider_id)
        change = providers.issuer_change_impact(s, provider_id, issuer) if issuer else 0
        return {"issuer_change": change, **asdict(removal)}


@router.delete("/admin/providers/{provider_id}", summary="Remove a provider with its links")
def remove_provider(provider_id: Annotated[int, Path(ge=1, le=2**31)], operator: OperatorAccount) -> dict[str, Any]:
    with store() as s:
        try:
            impact = providers.remove(s, provider_id)
        except ProviderInvalid as exc:
            raise _form_error(exc) from exc
    logger.info("OIDC provider %s removed by=%s", provider_id, operator.name)
    return asdict(impact)


# --- Start and return ----------------------------------------------------------------------------------------------


def _brake_key(request: Request) -> str:
    return "oidc:" + client_ip(request)


@router.get("/{slug}/start", summary="Send the browser to the provider (no sign-in needed)")
async def start(
    slug: Annotated[str, Path(max_length=40)],
    request: Request,
    invite: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_-]+$")] = None,
    next_page: Annotated[str | None, Query(alias="next", max_length=40)] = None,
) -> RedirectResponse:
    # Public: this is the sign-in button. Failures land on the sign-in page with a code; a foreign ``next`` is
    # dropped here without a word, the sign-in itself goes on.
    with store() as s:
        try:
            if brake.wait_seconds(_brake_key(request)):
                raise OidcError("too_many_attempts", "sender is braked")
            target = await flow.begin(s, slug, request_base=_base(request), invite=invite)
        except OidcError as exc:
            return _redirect(flow.refused(exc, None), request)
    response = _redirect(target, request)
    page = safe_next(next_page)
    if page and page != "/":
        response.set_cookie(NEXT_COOKIE, _pack_next(page), max_age=NEXT_MINUTES * 60, path=current().cookie_path,
                            httponly=True, samesite="lax", secure=_secure(request))
    else:
        _drop_next(response, request)
    return response


async def _returned(slug: str, request: Request) -> RedirectResponse:
    """The return from the provider. The order is the module's: the provider's own error, our cookie and state, the
    single use of the state, then the provider; nothing is written before all that passed."""
    cookie = request.cookies.get(current().cookie_name)
    key = _brake_key(request)
    with store() as s:
        try:
            if brake.wait_seconds(key):
                raise OidcError("too_many_attempts", "sender is braked")
            arrival = await flow.finish(s, slug, cookie=cookie, params=dict(request.query_params))
            if arrival.started.purpose == "link":
                signed_in = session_account(s.db, request.cookies.get(SESSION_COOKIE))
                oidc_accounts.link(s, arrival, signed_in.id if signed_in else None)
                brake.succeeded(key)
                response = _redirect(flow.arrived(arrival, flow.linked_page(slug)), request)
                _drop_next(response, request)
                return response
            signed = oidc_accounts.sign_in(s, arrival)
        except OidcError as exc:
            if exc.code not in FREE_REFUSALS:
                brake.failed(key)
            response = _redirect(flow.refused(exc, cookie), request)
            _drop_next(response, request)
            return response
        brake.succeeded(key)
        row = s.db.get(Account, signed.account_id)
        assert row is not None
        logs.set_actor(row.name)
        if signed.second_factor_due:
            # The provider is not trusted with the second factor and the account has one here: the same waiting
            # sign-in as after the password, without a vault key (nothing here knows the password). The code step
            # of the sign-in page finishes it.
            response = _redirect(flow.arrived(arrival, CODE_STEP), request)
            _set_pending_cookie(response, request, totp.start_pending(row.id, None))
            _drop_next(response, request)
            logger.info("Provider accepted, second factor waiting name=%s", row.name)
            return response
        page = _read_next(request.cookies.get(NEXT_COOKIE))
        response = _redirect(flow.arrived(arrival, page), request)
        _drop_next(response, request)
        token = start_session(s.db, row, client_ip(request), request.headers.get("user-agent", ""))
        _set_cookie(response, request, token)
        return response


@router.get("/callback", summary="The return from the provider of the entry oidc (the address before the list)")
async def legacy_callback(request: Request) -> RedirectResponse:
    return await _returned("oidc", request)


@router.get("/{slug}/callback", summary="The return from the provider (no sign-in needed)")
async def callback(slug: Annotated[str, Path(max_length=40)], request: Request) -> RedirectResponse:
    return await _returned(slug, request)


# --- My account ----------------------------------------------------------------------------------------------------


@router.get("/me", summary="The active providers and whether the own account is linked to each")
def my_providers(account: CurrentAccount) -> list[dict[str, Any]]:
    with store() as s:
        linked = {link.provider_id for link in s.links_of_account(account.id)}
        return [
            {"slug": entry.slug, "label": entry.label, "linked": entry.id in linked}
            for entry in s.list_providers()
            if entry.enabled
        ]


@router.post("/{slug}/link", summary="Start linking the own account to a provider; needs the password")
async def link_start(
    slug: Annotated[str, Path(max_length=40)],
    payload: PasswordIn,
    request: Request,
    response: Response,
    account: CurrentAccount,
) -> dict[str, str]:
    """Linking hands the account to whoever the browser is at the provider, so it is a POST with the CSRF header and
    the password: a page elsewhere cannot start it, and neither can somebody at an unattended browser. The browser
    then goes to the address in the answer; the return finishes the link."""
    with store() as s:
        row = s.db.get(Account, account.id)
        assert row is not None
        # The same brake as before start and return (blueprint 02, "Ablauf" 4): a braked sender links nothing.
        wait = brake.wait_seconds(_brake_key(request))
        if wait:
            raise fehler("too_many_attempts", "Too many attempts. Try again later.", 429, retry_after=wait)
        if row.sign_in != SIGN_IN_PASSWORD:
            raise fehler("oidc_only_account", "This account has no password; it signs in through a provider.", 409)
        reauth_guard(request, row)
        if not accounts.check_password(row, payload.password):
            reauth_failed(request, s.db, row)
            raise fehler("wrong_password", "The password is wrong.", 401)
        reauth_succeeded(request, s.db, row)
        try:
            target = await flow.begin(s, slug, request_base=_base(request), link_account_id=row.id)
        except OidcError as exc:
            raise fehler(exc.code, exc.message, 409) from exc
    cookie = target.cookie
    response.set_cookie(cookie.name, cookie.value, max_age=cookie.max_age, path=cookie.path, httponly=True,
                        samesite="lax", secure=cookie.secure or _secure(request))
    logger.info("Account %s starts linking to provider %s", account.name, slug)
    return {"url": target.location}


@router.delete("/{slug}/link", status_code=204, summary="Unlink the own account from a provider")
def unlink(slug: Annotated[str, Path(max_length=40)], account: CurrentAccount) -> None:
    with store() as s:
        entry = s.provider_by_slug(slug)
        if entry is None:
            raise fehler("oidc_not_configured", "This provider is not set up.", 404)
        try:
            oidc_accounts.unlink(s, account.id, entry.id)
        except OidcError as exc:
            raise fehler(exc.code, exc.message, 409) from exc


class OperatorUnlinkIn(BaseModel):
    password: str = Field(max_length=200)


@router.delete(
    "/admin/accounts/{account_id}/links/{provider_id}",
    status_code=204,
    summary="Operator: unlink an account from a provider (with the own password)",
)
def operator_unlink(
    account_id: Annotated[int, Path(ge=1, le=2**31)],
    provider_id: Annotated[int, Path(ge=1, le=2**31)],
    payload: OperatorUnlinkIn,
    request: Request,
    operator: OperatorAccount,
) -> None:
    with store() as s:
        me = s.db.get(Account, operator.id)
        assert me is not None
        if me.sign_in != SIGN_IN_PASSWORD:
            raise fehler("oidc_account", "This account signs in through OIDC.", 409)
        reauth_guard(request, me)
        if not accounts.check_password(me, payload.password):
            reauth_failed(request, s.db, me)
            raise fehler("wrong_password", "The password is wrong.", 401)
        reauth_succeeded(request, s.db, me)
        entry = s.get_provider(provider_id)
        target = s.db.get(Account, account_id)
        if target is None or entry is None:
            raise fehler("not_found", "Not found.", 404)
        try:
            removed = oidc_accounts.unlink(s, account_id, provider_id)
        except OidcError as exc:
            raise fehler(exc.code, exc.message, 409) from exc
        if not removed:
            raise fehler("not_found", "Not found.", 404)
        if account_id != operator.id:
            # Never unseen (blueprint 01): the account finds it on its own page, with the provider's name.
            s.db.refresh(target)
            oidc_store.note_unlinked(target, entry.label)
            s.db.commit()
    if account_id != operator.id:
        # Whoever had come in through that identity is out; with the password they come back in.
        end_ssh_sessions(account_id, "signed_out")
    logger.warning("Operator unlinked an account from provider %s name=%s by=%s", entry.slug, target.name,
                   operator.name)


# --- authentik -----------------------------------------------------------------------------------------------------


@router.post("/authentik/setup", summary="Set up provider and application in authentik with a one-time token")
async def authentik_setup(payload: SetupIn, operator: OperatorAccount, request: Request) -> dict[str, Any]:
    # Always 200 with the steps that ran, a failed one included: the page says why in the operator's language.
    logger.info("authentik setup started by=%s", operator.name)
    with store() as s:
        result = await authentik.setup(
            s, payload.url, payload.token, _base(request), confirm_issuer_change=payload.confirm_issuer_change
        )
    return result.as_dict()


@router.get("/authentik/blueprint", summary="Download a blueprint that creates the same objects in authentik")
def authentik_blueprint(_operator: OperatorAccount, request: Request) -> Response:
    with store() as s:
        text = authentik.blueprint(authentik.blueprint_redirect(s, _base(request)))
    return Response(
        content=text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{authentik.blueprint_filename()}"'},
    )

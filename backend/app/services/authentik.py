"""The authentik button: nextrmnl sets up its own OIDC provider and application in authentik.

The operator hands over the address of authentik and a one-time API token. nextrmnl then does through the
authentik API v3 what one would otherwise click together in a dozen forms: a signing key, a scope mapping that
vouches for the email address, the provider, the application, and finally its own settings. Every step reports
whether it succeeded and, if not, what authentik answered. The first failure stops the run; what came before
stays, and running the button again updates instead of duplicating.

The token is used for these calls only. It is never stored and never logged; the answer to the operator names
HTTP status and response text of a failure, never the token.

The blueprint is the way for operators who would rather not hand over a token: a YAML file for authentik's
blueprint import that creates the same objects, with the default self-signed certificate as signing key
because a blueprint cannot generate one.

Written against the authentik API conventions of 2024.x to 2026.x (list endpoints with ``results``,
``redirect_uris`` as a list of objects, ``invalidation_flow`` required on providers). Measured against
authentik 2026.8: since 2026.8 a provider carries ``grant_types``, and the authorize endpoint refuses every
request whose grant is not listed there with ``invalid_request``. A provider created through the API without
the field gets an empty list, so the button names the one grant nextrmnl uses. Older versions ignore the field.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .. import crypto
from . import oidc, settings_service

logger = logging.getLogger("nextrmnl.authentik")

NAME = "nextrmnl"
SLUG = "nextrmnl"
MAPPING_NAME = "nextrmnl email_verified"
#: authentik's own mappings for the openid and profile scopes, found by their managed name.
MANAGED_OPENID = "goauthentik.io/providers/oauth2/scope-openid"
MANAGED_PROFILE = "goauthentik.io/providers/oauth2/scope-profile"
#: authentik's email mapping reports ``email_verified: false`` since 2025.10; nextrmnl needs a true one and
#: replaces it with a mapping of its own under the same scope name.
MAPPING_EXPRESSION = 'return {"email": request.user.email, "email_verified": True}'
#: The grants the provider may hand out. nextrmnl runs the authorization code flow and nothing else: no refresh
#: tokens, no client credentials, no device codes.
GRANT_TYPES = ("authorization_code",)
PREFERRED_AUTHORIZATION_FLOW = "default-provider-authorization-implicit-consent"
PREFERRED_INVALIDATION_FLOW = "default-provider-invalidation-flow"
CERT_VALIDITY_DAYS = 3650
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
#: How much of an error answer goes back to the operator. authentik's error pages are long.
DETAIL_MAX = 300

STEP_KEYS = ("reached", "signingKey", "mapping", "provider", "application", "filled")

#: Tests set an ``httpx.MockTransport`` here.
transport_for_tests: httpx.BaseTransport | None = None


class StepFailed(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class Step:
    key: str
    ok: bool
    detail: str = ""


@dataclass
class SetupResult:
    steps: list[Step] = field(default_factory=list)
    client_id: str = ""
    issuer: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [{"key": step.key, "ok": step.ok, "detail": step.detail} for step in self.steps],
            "client_id": self.client_id,
            "issuer": self.issuer,
        }


class _Api:
    """The few calls nextrmnl needs, with the token in the Authorization header and nowhere else."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=TIMEOUT,
            transport=transport_for_tests,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, path: str, *, params: dict[str, str] | None = None, body: Any = None) -> Any:
        url = f"{self.base_url}/api/v3{path}"
        try:
            response = await self._client.request(method, url, params=params, json=body)
        except httpx.HTTPError as error:
            kind = error.__class__.__name__
            raise StepFailed(f"{method} {path}: authentik at {self.base_url} not reachable ({kind})") from error
        except Exception as error:
            # ``httpx.InvalidURL`` is not an HTTPError; the address comes from the operator.
            kind = error.__class__.__name__
            raise StepFailed(f"{method} {path}: the address {self.base_url!r} cannot be used ({kind})") from error
        if not response.is_success:
            text = response.text.strip().replace("\n", " ")[:DETAIL_MAX]
            raise StepFailed(f"{method} {path} answered {response.status_code}: {text or 'no body'}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise StepFailed(f"{method} {path} answered {response.status_code} without JSON") from error

    async def find_one(self, path: str, params: dict[str, str], key: str, value: str) -> dict[str, Any] | None:
        """The one list entry whose ``key`` equals ``value``. The filter parameter narrows the list; the exact
        comparison here makes sure a looser filter or an ignored parameter does not hand back a stranger."""
        data = await self.call("GET", path, params=params)
        results = data.get("results", []) if isinstance(data, dict) else []
        for entry in results:
            if isinstance(entry, dict) and str(entry.get(key) or "") == value:
                return entry
        return None


def _pk(entry: dict[str, Any], what: str) -> Any:
    pk = entry.get("pk")
    if pk in (None, ""):
        raise StepFailed(f"{what} has no pk in authentik's answer")
    return pk


async def _reached(api: _Api) -> str:
    data = await api.call("GET", "/admin/version/")
    version = str((data or {}).get("version_current") or "") if isinstance(data, dict) else ""
    return f"authentik {version}" if version else "authentik reached, version unknown"


async def _signing_key(api: _Api) -> tuple[Any, str]:
    existing = await api.find_one("/crypto/certificatekeypairs/", {"name": NAME}, "name", NAME)
    if existing is not None:
        return _pk(existing, "certificate"), f"using the existing certificate {NAME!r}"
    created = await api.call(
        "POST",
        "/crypto/certificatekeypairs/generate/",
        body={"common_name": NAME, "subject_alt_name": "", "validity_days": CERT_VALIDITY_DAYS, "alg": "rsa"},
    )
    if not isinstance(created, dict):
        raise StepFailed("certificate generation answered without an object")
    return _pk(created, "certificate"), f"generated the certificate {NAME!r} ({CERT_VALIDITY_DAYS} days)"


async def _mappings(api: _Api) -> tuple[list[Any], str]:
    path = "/propertymappings/provider/scope/"
    own = await api.find_one(path, {"name": MAPPING_NAME}, "name", MAPPING_NAME)
    if own is None:
        own = await api.call(
            "POST",
            path,
            body={
                "name": MAPPING_NAME,
                "scope_name": "email",
                "description": "Email address, vouched for by authentik. Created by nextrmnl.",
                "expression": MAPPING_EXPRESSION,
            },
        )
        if not isinstance(own, dict):
            raise StepFailed("creating the scope mapping answered without an object")
        note = f"created the scope mapping {MAPPING_NAME!r}"
    else:
        note = f"using the existing scope mapping {MAPPING_NAME!r}"
    ids = [_pk(own, "scope mapping")]
    for managed in (MANAGED_OPENID, MANAGED_PROFILE):
        default = await api.find_one(path, {"managed": managed}, "managed", managed)
        if default is None:
            raise StepFailed(f"authentik's default scope mapping {managed!r} was not found")
        ids.append(_pk(default, "scope mapping"))
    return ids, note


async def _flow(api: _Api, designation: str, preferred: str) -> Any:
    data = await api.call("GET", "/flows/instances/", params={"designation": designation})
    entries = data.get("results", []) if isinstance(data, dict) else []
    results = [entry for entry in entries if isinstance(entry, dict)]
    if not results:
        raise StepFailed(f"no flow with designation {designation!r} in authentik")
    for entry in results:
        if entry.get("slug") == preferred:
            return _pk(entry, "flow")
    return _pk(results[0], "flow")


async def _provider(api: _Api, redirect_uri: str, signing_key: Any, mappings: list[Any]) -> tuple[Any, str, str, str]:
    authorization = await _flow(api, "authorization", PREFERRED_AUTHORIZATION_FLOW)
    invalidation = await _flow(api, "invalidation", PREFERRED_INVALIDATION_FLOW)
    body = {
        "name": NAME,
        "authorization_flow": authorization,
        "invalidation_flow": invalidation,
        "client_type": "confidential",
        "grant_types": list(GRANT_TYPES),
        "redirect_uris": [{"matching_mode": "strict", "url": redirect_uri}],
        "signing_key": signing_key,
        "sub_mode": "user_uuid",
        "property_mappings": mappings,
        "include_claims_in_id_token": True,
    }
    existing = await api.find_one("/providers/oauth2/", {"name": NAME}, "name", NAME)
    if existing is None:
        answer = await api.call("POST", "/providers/oauth2/", body=body)
        note = f"created the provider {NAME!r}"
    else:
        answer = await api.call("PATCH", f"/providers/oauth2/{_pk(existing, 'provider')}/", body=body)
        note = f"updated the existing provider {NAME!r}"
    if not isinstance(answer, dict):
        raise StepFailed("the provider call answered without an object")
    client_id = str(answer.get("client_id") or "")
    client_secret = str(answer.get("client_secret") or "")
    if not client_id or not client_secret:
        raise StepFailed("authentik's provider answer carries no client_id or client_secret")
    return _pk(answer, "provider"), client_id, client_secret, note


async def _application(api: _Api, provider_pk: Any) -> str:
    body = {"name": NAME, "slug": SLUG, "provider": provider_pk}
    existing = await api.find_one("/core/applications/", {"slug": SLUG}, "slug", SLUG)
    if existing is None:
        await api.call("POST", "/core/applications/", body=body)
        return f"created the application {SLUG!r}"
    await api.call("PATCH", f"/core/applications/{SLUG}/", body=body)
    return f"updated the existing application {SLUG!r}"


def issuer_for(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/application/o/{SLUG}/"


async def setup(db: Session, base_url: str, token: str, redirect_uri: str) -> SetupResult:
    """The whole run, step by step. Stops at the first failure; the result lists every step that ran."""
    result = SetupResult(issuer=issuer_for(base_url))
    api = _Api(base_url, token)
    signing_key: Any = None
    mappings: list[Any] = []
    provider_pk: Any = None
    client_id = client_secret = ""
    try:
        for key in STEP_KEYS:
            try:
                if key == "reached":
                    detail = await _reached(api)
                elif key == "signingKey":
                    signing_key, detail = await _signing_key(api)
                elif key == "mapping":
                    mappings, detail = await _mappings(api)
                elif key == "provider":
                    provider_pk, client_id, client_secret, detail = await _provider(
                        api, redirect_uri, signing_key, mappings
                    )
                    result.client_id = client_id
                elif key == "application":
                    detail = await _application(api, provider_pk)
                else:
                    detail = await _fill(db, result.issuer, client_id, client_secret)
            except StepFailed as error:
                result.steps.append(Step(key, False, error.detail))
                logger.warning("authentik setup stopped at step %s: %s", key, error.detail)
                break
            result.steps.append(Step(key, True, detail))
            logger.info("authentik setup step %s done: %s", key, detail)
    finally:
        await api.close()
    return result


async def _fill(db: Session, issuer: str, client_id: str, client_secret: str) -> str:
    """Store the settings, then confirm the issuer with one discovery call.

    Stored first: the values are what authentik handed out, and a failed discovery usually means nextrmnl
    cannot reach authentik under this address, which the operator fixes at the network or with a corrected
    address. The stored configuration can be removed with DELETE /api/oidc/config at any time.
    """
    settings_service.save(
        db,
        {
            "oidc_issuer": issuer,
            "oidc_client_id": client_id,
            "oidc_client_secret_enc": crypto.encrypt_secret(client_secret),
            "oidc_provider_name": "authentik",
        },
    )
    oidc.clear_cache()
    try:
        await oidc.discovery(issuer, fresh=True)
    except oidc.OidcError as error:
        raise StepFailed(f"settings stored, but discovery at {issuer} failed: {error.code}") from error
    return f"settings stored and discovery at {issuer} confirmed"


# ---------------------------------------------------------------------------
# The blueprint
# ---------------------------------------------------------------------------


def blueprint(redirect_uri: str) -> str:
    """A blueprint (schema version 1) that creates the same objects as the button.

    ``!Find`` and ``!KeyOf`` are authentik's YAML tags; the file is assembled as text so they come out as tags,
    not as quoted strings. The redirect URI is emitted as a JSON string, which is a valid double-quoted YAML
    scalar whatever characters it carries.
    """
    redirect = json.dumps(redirect_uri)
    expression = MAPPING_EXPRESSION
    return f"""# nextrmnl: OpenID Connect provider and application for authentik.
#
# Apply it under Customization, Blueprints (create a blueprint from this file) or drop it into the
# blueprints/custom/ directory of the authentik worker. Afterwards copy client id and client secret from the
# provider "nextrmnl" (Applications, Providers) into nextrmnl under Settings, Sign-in, and enter the issuer
# <authentik address>/application/o/nextrmnl/.
#
# The signing key is authentik's default self-signed certificate: a blueprint cannot generate one. Pick a
# different certificate at the provider afterwards if you prefer.
#
# Written against the authentik blueprint schema v1 (2024.x to 2026.x). grant_types exists since authentik 2026.8,
# where a provider without it refuses every sign-in; older versions ignore the line.
version: 1
metadata:
  name: {NAME}
  labels:
    blueprints.goauthentik.io/description: OpenID Connect provider and application for nextrmnl
entries:
  - model: authentik_providers_oauth2.scopemapping
    state: present
    id: nextrmnl-email-verified
    identifiers:
      name: {MAPPING_NAME}
    attrs:
      scope_name: email
      description: Email address, vouched for by authentik. Created for nextrmnl.
      expression: |
        {expression}
  - model: authentik_providers_oauth2.oauth2provider
    state: present
    id: nextrmnl-provider
    identifiers:
      name: {NAME}
    attrs:
      authorization_flow: !Find [authentik_flows.flow, [slug, {PREFERRED_AUTHORIZATION_FLOW}]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, {PREFERRED_INVALIDATION_FLOW}]]
      client_type: confidential
      grant_types:
        - {GRANT_TYPES[0]}
      redirect_uris:
        - matching_mode: strict
          url: {redirect}
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, authentik Self-signed Certificate]]
      sub_mode: user_uuid
      include_claims_in_id_token: true
      property_mappings:
        - !Find [authentik_providers_oauth2.scopemapping, [managed, {MANAGED_OPENID}]]
        - !Find [authentik_providers_oauth2.scopemapping, [managed, {MANAGED_PROFILE}]]
        - !KeyOf nextrmnl-email-verified
  - model: authentik_core.application
    state: present
    identifiers:
      slug: {SLUG}
    attrs:
      name: {NAME}
      provider: !KeyOf nextrmnl-provider
"""

"""The authentik button against a fake authentik API v3, and the blueprint download.

The fake answers the calls in the order the service makes them and records every request, so the tests can
check that the token travels only in the Authorization header, that the steps stop at the first failure and
that an existing provider is updated instead of duplicated. No network anywhere.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.services import authentik, oidc, settings_service
from tests.conftest import UI, invite_member

URL = "https://auth.example.com"
TOKEN = "one-time-token-that-must-stay-out-of-everything"
ISSUER = f"{URL}/application/o/nextrmnl/"
REDIRECT = "http://testserver/api/oidc/callback"


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, str]
    auth: str
    body: dict | None


@dataclass
class FakeAuthentik:
    """authentik as a MockTransport handler. ``existing`` says which objects are already there."""

    existing: set[str] = field(default_factory=set)
    fail: tuple[str, str, int] | None = None
    discovery_ok: bool = True
    calls: list[Recorded] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "auth.example.com":
            raise httpx.ConnectError("no such host")
        method, path = request.method, request.url.path
        query = dict(request.url.params.items())
        body = json.loads(request.content) if request.content else None
        self.calls.append(Recorded(method, path, query, request.headers.get("authorization", ""), body))
        if self.fail and (method, path) == self.fail[:2]:
            return httpx.Response(self.fail[2], text="<html>authentik error page</html>")
        if path == "/application/o/nextrmnl/.well-known/openid-configuration":
            if not self.discovery_ok:
                return httpx.Response(404, text="not found")
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}authorize/",
                    "token_endpoint": f"{URL}/application/o/token/",
                    "jwks_uri": f"{ISSUER}jwks/",
                },
            )
        if not path.startswith("/api/v3/"):
            return httpx.Response(404)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(403, json={"detail": "Authentication credentials were not provided."})
        return self._api(method, path[len("/api/v3") :], query, body)

    def _api(self, method: str, path: str, query: dict[str, str], body: dict | None) -> httpx.Response:
        if (method, path) == ("GET", "/admin/version/"):
            return httpx.Response(200, json={"version_current": "2026.8.1", "version_latest": "2026.8.1"})
        if (method, path) == ("GET", "/crypto/certificatekeypairs/"):
            rows = [{"pk": "cert-uuid", "name": "nextrmnl"}] if "cert" in self.existing else []
            return httpx.Response(200, json={"results": rows})
        if (method, path) == ("POST", "/crypto/certificatekeypairs/generate/"):
            return httpx.Response(200, json={"pk": "cert-uuid", "name": body["common_name"]})
        if (method, path) == ("GET", "/propertymappings/provider/scope/"):
            rows = [
                {"pk": "map-openid", "managed": "goauthentik.io/providers/oauth2/scope-openid", "name": "authentik default OAuth Mapping: OpenID 'openid'"},
                {"pk": "map-profile", "managed": "goauthentik.io/providers/oauth2/scope-profile", "name": "authentik default OAuth Mapping: OpenID 'profile'"},
                {"pk": "map-email", "managed": "goauthentik.io/providers/oauth2/scope-email", "name": "authentik default OAuth Mapping: OpenID 'email'"},
            ]
            if "mapping" in self.existing:
                rows.append({"pk": "map-own", "managed": None, "name": "nextrmnl email_verified"})
            if "name" in query:
                rows = [row for row in rows if row["name"] == query["name"]]
            if "managed" in query:
                rows = [row for row in rows if row["managed"] == query["managed"]]
            return httpx.Response(200, json={"results": rows})
        if (method, path) == ("POST", "/propertymappings/provider/scope/"):
            return httpx.Response(201, json={"pk": "map-own", "name": body["name"], "scope_name": body["scope_name"]})
        if (method, path) == ("GET", "/flows/instances/"):
            if query.get("designation") == "authorization":
                rows = [
                    {"pk": "flow-explicit", "slug": "default-provider-authorization-explicit-consent"},
                    {"pk": "flow-implicit", "slug": "default-provider-authorization-implicit-consent"},
                ]
            else:
                rows = [{"pk": "flow-invalidation", "slug": "default-provider-invalidation-flow"}]
            return httpx.Response(200, json={"results": rows})
        if (method, path) == ("GET", "/providers/oauth2/"):
            rows = [{"pk": 7, "name": "nextrmnl"}] if "provider" in self.existing else []
            return httpx.Response(200, json={"results": rows})
        if (method, path) in (("POST", "/providers/oauth2/"), ("PATCH", "/providers/oauth2/7/")):
            status = 201 if method == "POST" else 200
            return httpx.Response(status, json={**body, "pk": 7, "client_id": "generated-client-id", "client_secret": "generated-secret"})
        if (method, path) == ("GET", "/core/applications/"):
            rows = [{"pk": "app-uuid", "slug": "nextrmnl", "name": "nextrmnl"}] if "application" in self.existing else []
            return httpx.Response(200, json={"results": rows})
        if (method, path) in (("POST", "/core/applications/"), ("PATCH", "/core/applications/nextrmnl/")):
            return httpx.Response(201 if method == "POST" else 200, json={**body, "pk": "app-uuid"})
        return httpx.Response(404, json={"detail": f"no fake answer for {method} {path}"})


@pytest.fixture
def fake() -> Iterator[FakeAuthentik]:
    server = FakeAuthentik()
    transport = httpx.MockTransport(server)
    authentik.transport_for_tests = transport
    oidc.transport_for_tests = transport
    oidc.clear_cache()
    yield server
    authentik.transport_for_tests = None
    oidc.transport_for_tests = None
    oidc.clear_cache()


def run_setup(client: TestClient, url: str = URL, token: str = TOKEN) -> dict:
    response = client.post("/api/oidc/authentik/setup", json={"url": url, "token": token}, headers=UI)
    assert response.status_code == 200, response.text
    return response.json()


def steps(result: dict) -> list[tuple[str, bool]]:
    return [(step["key"], step["ok"]) for step in result["steps"]]


def stored() -> dict:
    with SessionLocal() as db:
        return {key: settings_service.get(db, key) for key in ("oidc_issuer", "oidc_client_id", "oidc_client_secret_enc", "oidc_provider_name")}


# ---------------------------------------------------------------------------
# The button
# ---------------------------------------------------------------------------


def test_setup_creates_everything_and_fills_the_configuration(
    client: TestClient, operator: dict, fake: FakeAuthentik
) -> None:
    result = run_setup(client)
    assert steps(result) == [(key, True) for key in authentik.STEP_KEYS]
    assert result["client_id"] == "generated-client-id"
    assert result["issuer"] == ISSUER
    assert "2026.8.1" in result["steps"][0]["detail"]

    # Stored, encrypted, and the sign-in page sees the button.
    values = stored()
    assert values["oidc_issuer"] == ISSUER and values["oidc_client_id"] == "generated-client-id"
    assert values["oidc_provider_name"] == "authentik"
    assert values["oidc_client_secret_enc"] and "generated-secret" not in values["oidc_client_secret_enc"]
    assert client.get("/api/oidc/state").json() == {"enabled": True, "provider_name": "authentik"}
    assert client.get("/api/oidc/config").json()["configured"] is True

    # The provider was created with what the task prescribes.
    created = [call for call in fake.calls if (call.method, call.path) == ("POST", "/api/v3/providers/oauth2/")]
    assert len(created) == 1
    body = created[0].body
    assert body["name"] == "nextrmnl" and body["client_type"] == "confidential"
    # authentik 2026.8 refuses every authorize request whose grant is not listed on the provider.
    assert body["grant_types"] == ["authorization_code"]
    assert body["redirect_uris"] == [{"matching_mode": "strict", "url": REDIRECT}]
    assert body["signing_key"] == "cert-uuid" and body["sub_mode"] == "user_uuid"
    assert body["authorization_flow"] == "flow-implicit" and body["invalidation_flow"] == "flow-invalidation"
    assert sorted(body["property_mappings"]) == ["map-openid", "map-own", "map-profile"]
    generated = [call for call in fake.calls if call.path == "/api/v3/crypto/certificatekeypairs/generate/"]
    assert generated[0].body["common_name"] == "nextrmnl" and generated[0].body["validity_days"] == 3650
    mapping = [call for call in fake.calls if (call.method, call.path) == ("POST", "/api/v3/propertymappings/provider/scope/")]
    assert mapping[0].body["scope_name"] == "email" and '"email_verified": True' in mapping[0].body["expression"]
    application = [call for call in fake.calls if (call.method, call.path) == ("POST", "/api/v3/core/applications/")]
    assert application[0].body == {"name": "nextrmnl", "slug": "nextrmnl", "provider": 7}
    assert not any(call.method == "PATCH" for call in fake.calls)


def test_token_travels_only_in_the_authorization_header(
    client: TestClient, operator: dict, fake: FakeAuthentik, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        result = run_setup(client)
    api_calls = [call for call in fake.calls if call.path.startswith("/api/v3/")]
    assert api_calls
    for call in api_calls:
        assert call.auth == f"Bearer {TOKEN}"
        assert TOKEN not in call.path and TOKEN not in str(call.query) and TOKEN not in json.dumps(call.body)
    assert TOKEN not in caplog.text
    assert TOKEN not in json.dumps(result)
    # Neither the token nor the secret ends up in the answer or the log.
    assert "generated-secret" not in json.dumps(result)
    assert "generated-secret" not in caplog.text


def test_steps_stop_at_the_first_failure_and_nothing_is_stored(
    client: TestClient, operator: dict, fake: FakeAuthentik
) -> None:
    fake.fail = ("POST", "/api/v3/crypto/certificatekeypairs/generate/", 500)
    result = run_setup(client)
    assert steps(result) == [("reached", True), ("signingKey", False)]
    failed = result["steps"][1]["detail"]
    assert "500" in failed and "generate" in failed
    assert TOKEN not in failed
    assert result["client_id"] == ""
    assert stored()["oidc_issuer"] == ""
    assert client.get("/api/oidc/state").json()["enabled"] is False
    # Nothing beyond the failed call was tried.
    assert not any(call.path.startswith("/api/v3/propertymappings") for call in fake.calls)


def test_unreachable_authentik_fails_at_the_first_step(client: TestClient, operator: dict, fake: FakeAuthentik) -> None:
    result = run_setup(client, url="https://nowhere.example.com")
    assert steps(result) == [("reached", False)]
    assert "not reachable" in result["steps"][0]["detail"]


def test_wrong_token_is_reported_without_repeating_it(client: TestClient, operator: dict, fake: FakeAuthentik) -> None:
    result = run_setup(client, token="wrong-token")
    assert steps(result) == [("reached", False)]
    assert "403" in result["steps"][0]["detail"]
    assert "wrong-token" not in json.dumps(result)


def test_existing_objects_are_updated_not_duplicated(client: TestClient, operator: dict, fake: FakeAuthentik) -> None:
    fake.existing = {"cert", "mapping", "provider", "application"}
    result = run_setup(client)
    assert steps(result) == [(key, True) for key in authentik.STEP_KEYS]
    methods = [(call.method, call.path) for call in fake.calls]
    assert ("PATCH", "/api/v3/providers/oauth2/7/") in methods
    assert ("PATCH", "/api/v3/core/applications/nextrmnl/") in methods
    assert ("POST", "/api/v3/providers/oauth2/") not in methods
    assert ("POST", "/api/v3/core/applications/") not in methods
    assert ("POST", "/api/v3/crypto/certificatekeypairs/generate/") not in methods
    assert ("POST", "/api/v3/propertymappings/provider/scope/") not in methods
    assert "existing" in result["steps"][3]["detail"] and "existing" in result["steps"][4]["detail"]
    patched = next(call for call in fake.calls if call.method == "PATCH" and "providers" in call.path)
    assert patched.body["redirect_uris"] == [{"matching_mode": "strict", "url": REDIRECT}]
    # A provider made by an older button has no grant types; running the button again must add them.
    assert patched.body["grant_types"] == ["authorization_code"]
    assert sorted(patched.body["property_mappings"]) == ["map-openid", "map-own", "map-profile"]


def test_failed_discovery_after_storing_is_reported(client: TestClient, operator: dict, fake: FakeAuthentik) -> None:
    """The values come from authentik and are stored; whether nextrmnl can reach the issuer is reported."""
    fake.discovery_ok = False
    result = run_setup(client)
    assert steps(result)[:5] == [(key, True) for key in authentik.STEP_KEYS[:5]]
    assert steps(result)[5] == ("filled", False)
    assert "discovery" in result["steps"][5]["detail"]
    assert stored()["oidc_client_id"] == "generated-client-id"


def test_setup_is_operator_only_and_checks_the_address(client: TestClient, operator: dict, fake: FakeAuthentik) -> None:
    member = invite_member(client)
    response = member.post("/api/oidc/authentik/setup", json={"url": URL, "token": TOKEN}, headers=UI)
    assert response.status_code == 403
    bad = client.post("/api/oidc/authentik/setup", json={"url": "auth.example.com", "token": TOKEN}, headers=UI)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "url_invalid"
    assert fake.calls == []


# ---------------------------------------------------------------------------
# The blueprint
# ---------------------------------------------------------------------------


class _TagLoader(yaml.SafeLoader):
    """Reads authentik's ``!Find`` and ``!KeyOf`` tags as plain values, enough to check the structure."""


_TagLoader.add_constructor("!Find", lambda loader, node: ("Find", loader.construct_sequence(node, deep=True)))
_TagLoader.add_constructor("!KeyOf", lambda loader, node: ("KeyOf", loader.construct_scalar(node)))


def test_blueprint_download_creates_the_same_objects(client: TestClient, operator: dict) -> None:
    response = client.get("/api/oidc/authentik/blueprint")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/yaml")
    assert response.headers["content-disposition"] == 'attachment; filename="nextrmnl-authentik.yaml"'
    text = response.text
    assert "!Find" in text and "!KeyOf" in text

    data = yaml.load(text, Loader=_TagLoader)
    assert data["version"] == 1
    models = [entry["model"] for entry in data["entries"]]
    assert models == [
        "authentik_providers_oauth2.scopemapping",
        "authentik_providers_oauth2.oauth2provider",
        "authentik_core.application",
    ]
    mapping, provider, application = data["entries"]
    assert mapping["identifiers"] == {"name": "nextrmnl email_verified"}
    assert mapping["attrs"]["scope_name"] == "email"
    assert '"email_verified": True' in mapping["attrs"]["expression"]
    attrs = provider["attrs"]
    assert attrs["client_type"] == "confidential" and attrs["sub_mode"] == "user_uuid"
    assert attrs["grant_types"] == ["authorization_code"]
    assert attrs["redirect_uris"] == [{"matching_mode": "strict", "url": REDIRECT}]
    assert attrs["authorization_flow"] == ("Find", ["authentik_flows.flow", ["slug", "default-provider-authorization-implicit-consent"]])
    assert attrs["signing_key"][0] == "Find" and attrs["signing_key"][1][0] == "authentik_crypto.certificatekeypair"
    assert ("KeyOf", "nextrmnl-email-verified") in attrs["property_mappings"]
    assert application["identifiers"] == {"slug": "nextrmnl"}
    assert application["attrs"]["provider"] == ("KeyOf", "nextrmnl-provider")

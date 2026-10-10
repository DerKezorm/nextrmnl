"""Helpers for nextrmnl's own tests around sign-in through a provider (the direct link, guest accounts, invitations,
the operator's list, the migration and the way back). The provider is the fake of the shared contract tests
(``tests/nexoidc``, ``oidc_fakes``): real RS256 tokens, discovery, keys, token and userinfo answered in memory,
never the network.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent / "nexoidc"))

import oidc_fakes as fakes

from app.main import app
from app.vendor import nexoidc
from app.vendor.nexoidc import attempt, protocol

#: The header every changing request of the interface carries (changes are refused without it).
UI = {"X-Requested-By": "nextrmnl"}
FakeProvider = fakes.FakeProvider


@pytest.fixture
def provider() -> Iterator[fakes.FakeProvider]:
    """A fake provider on a fake network for the whole test."""
    network = fakes.Network()
    fake = network.add(fakes.FakeProvider())
    # A test that needs a second provider adds it to the same network.
    fake.network = network  # type: ignore[attr-defined]
    nexoidc.use_transport(network.transport())
    protocol.clear_caches()
    attempt.forget_used_states()
    try:
        yield fake
    finally:
        nexoidc.use_transport(None)
        protocol.clear_caches()
        attempt.forget_used_states()


def fresh_browser(_client: TestClient | None = None) -> TestClient:
    """A browser without a session that does not follow redirects."""
    return TestClient(app, base_url="http://testserver", follow_redirects=False, headers=UI)


def add_provider(
    operator: TestClient, fake: fakes.FakeProvider, *, slug: str = "sso", label: str = "SSO", **form: Any
) -> dict[str, Any]:
    """The fake as an entry of the list, through the operator's route."""
    body = {
        "label": label,
        "slug": slug,
        "issuer": fake.issuer,
        "client_id": fake.client_id,
        "client_secret": fake.client_secret,
        **form,
    }
    answer = operator.post("/api/oidc/admin/providers", json=body, headers=UI)
    assert answer.status_code == 201, answer.text
    return answer.json()


def location(response: Any) -> str:
    assert response.status_code in (302, 303, 307), (response.status_code, response.text[:200])
    return str(response.headers["location"])


def error_in(response: Any) -> str | None:
    query = dict(item.split("=", 1) for item in urlsplit(location(response)).query.split("&") if "=" in item)
    return query.get("error")


def come_back(browser: TestClient, fake: fakes.FakeProvider, target: str) -> Any:
    """Through the provider and back to the callback the app sent it to."""
    params = fake.authorize(target)
    path = urlsplit(fakes.query_of(target)["redirect_uri"]).path
    return browser.get(f"{path}?{urlencode(params)}", follow_redirects=False)


def sign_in_via_oidc(
    browser: TestClient,
    fake: fakes.FakeProvider,
    *,
    slug: str = "sso",
    invite: str | None = None,
    next_page: str | None = None,
    **person: Any,
) -> Any:
    """``person`` signs in at the provider ``slug``; the answer of the return."""
    if person:
        fake.person = {"sub": "person-1", **person}
    params = {"invite": invite} if invite else {}
    if next_page is not None:
        params["next"] = next_page
    target = location(browser.get(f"/api/oidc/{slug}/start", params=params, follow_redirects=False))
    return come_back(browser, fake, target)


def link(member: TestClient, fake: fakes.FakeProvider, password: str, *, slug: str = "sso", **person: Any) -> Any:
    """The signed-in ``member`` links itself to ``person`` at the provider; the answer of the return."""
    if person:
        fake.person = {"sub": "person-1", **person}
    answer = member.post(f"/api/oidc/{slug}/link", json={"password": password}, headers=UI)
    assert answer.status_code == 200, answer.text
    return come_back(member, fake, answer.json()["url"])

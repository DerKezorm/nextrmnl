"""Contract, Bauplan 01 "Umstieg" 4 and 22 of Bauplan 05: up again after the way back.

The app mirrors the entry ``oidc`` into its old settings and the old column after every change, and stores a
fingerprint of what it wrote. At the next start (``adapter.run_migration``) it takes over what the version before the
list changed since (``migrate.resync``), and nothing else. ``adapter.legacy_setup`` between two starts is that older
version at work: it writes the old settings and subjects the way the older version did.

Only for apps that kept one provider in their settings (``legacy_model == "single"``); the others have no way back.
"""

from __future__ import annotations

from typing import Any

import oidc_contract
import oidc_fakes as fakes
from conftest import add_in_store
from oidc_contract import location, start


def _one_provider(adapter: Any) -> bool:
    if adapter.legacy_model != "single":
        assert adapter.legacy_model in ("list", "none")
        return False
    return True


def migrated(adapter: Any, sso: fakes.FakeProvider, **subjects: str) -> dict[str, int]:
    """alex (and bob, where there are two accounts) and the old provider, migrated: the entry ``oidc`` with alex
    linked as ``person-1``. Returns the account ids by name."""
    ids = {"alex": adapter.make_account("alex")}
    if not adapter.single_account:
        ids["bob"] = adapter.make_account("bob")
    older(adapter, sso, subjects={"alex": "person-1", **subjects})
    adapter.run_migration()
    return ids


def older(adapter: Any, sso: fakes.FakeProvider, *, subjects: dict[str, str], **settings: str) -> None:
    """The version before the list writes its settings (as given, else the fake's) and the subjects named."""
    values = {
        "issuer": sso.issuer,
        "client_id": sso.client_id,
        "client_secret": sso.client_secret,
        "label": "Company",
        **settings,
    }
    adapter.legacy_setup(subjects=subjects, **values)


def the_entry(adapter: Any) -> Any:
    with adapter.store() as store:
        return store.provider_by_slug("oidc")


def linked(adapter: Any) -> dict[str, str]:
    """The links of the entry ``oidc``: account name to subject."""
    with adapter.store() as store:
        entry = store.provider_by_slug("oidc")
        if entry is None:
            return {}
        return {store.account_state(link.account_id).name: link.subject for link in store.links_of_provider(entry.id)}


def change_entry(adapter: Any, oidc: Any, **values: Any) -> None:
    """The new version changes the entry ``oidc`` (through the store, as every route does; the commit mirrors)."""
    with adapter.store() as store:
        entry = store.provider_by_slug("oidc")
        store.update_provider(entry.id, oidc.providers.values_of(entry, **values))
        store.commit()


def test_22_up_again_what_the_new_version_did_stays_when_the_older_one_changed_nothing(
    adapter: Any, sso: fakes.FakeProvider, oidc: Any
) -> None:
    if not _one_provider(adapter):
        return
    ids = migrated(adapter, sso)
    adapter.run_migration()
    assert linked(adapter) == {"alex": "person-1"}
    # Unlinked here: a start without a change in the older version does not bring the link back.
    with adapter.store() as store:
        assert store.remove_link(store.provider_by_slug("oidc").id, ids["alex"])
        store.commit()
    adapter.run_migration()
    assert linked(adapter) == {}
    # Off here, and on without the second factor checked by the provider: the mirror wrote "no provider" itself, and
    # that is no change of the older version.
    change_entry(adapter, oidc, enabled=False)
    adapter.run_migration()
    assert the_entry(adapter) is not None and the_entry(adapter).enabled is False
    change_entry(adapter, oidc, enabled=True, trusts_second_factor=False)
    adapter.run_migration()
    entry = the_entry(adapter)
    assert (entry.enabled, entry.trusts_second_factor) == (True, False)
    # Removed here: it stays removed.
    with adapter.store() as store:
        store.delete_provider(store.provider_by_slug("oidc").id)
        store.commit()
    adapter.run_migration()
    assert the_entry(adapter) is None


def test_22_up_again_a_subject_the_older_version_emptied_or_set_is_taken_over(
    adapter: Any, sso: fakes.FakeProvider
) -> None:
    if not _one_provider(adapter):
        return
    migrated(adapter, sso)
    older(adapter, sso, subjects={"alex": ""})
    adapter.run_migration()
    assert linked(adapter) == {}
    browser, response = oidc_contract.round_trip(adapter, sso, "oidc", legacy=True)
    assert oidc_contract.error_in(response) == "oidc_no_account" and adapter.who(browser) is None
    older(adapter, sso, subjects={"alex": "person-7"})
    adapter.run_migration()
    assert linked(adapter) == {"alex": "person-7"}
    if adapter.single_account:
        return
    older(adapter, sso, subjects={"bob": "person-2"})
    adapter.run_migration()
    assert linked(adapter) == {"alex": "person-7", "bob": "person-2"}
    # A subject another account holds here: the holder keeps it, and alex loses person-7 all the same (the older
    # version gave it up; it must not sign in again).
    older(adapter, sso, subjects={"alex": "person-2"})
    adapter.run_migration()
    assert linked(adapter) == {"bob": "person-2"}
    assert adapter.legacy_read()["subjects"] == {"bob": "person-2"}


def test_22_up_again_a_subject_that_moved_or_swapped_is_taken_over_in_any_order_of_the_accounts(
    adapter: Any, sso: fakes.FakeProvider
) -> None:
    """alex was made before bob. A subject that moves from bob to alex, from alex to bob, and two that swap: each
    taken over, nobody keeps a subject the older version took from them."""
    if not _one_provider(adapter) or adapter.single_account:
        return
    cases = [
        ({"alex": "person-2", "bob": ""}, {"alex": "person-2"}),
        ({"alex": "", "bob": "person-1"}, {"bob": "person-1"}),
        ({"alex": "person-2", "bob": "person-1"}, {"alex": "person-2", "bob": "person-1"}),
    ]
    migrated(adapter, sso)
    for subjects, expected in cases:
        older(adapter, sso, subjects={"alex": "person-1", "bob": "person-2"})
        adapter.run_migration()
        assert linked(adapter) == {"alex": "person-1", "bob": "person-2"}
        older(adapter, sso, subjects=subjects)
        adapter.run_migration()
        assert linked(adapter) == expected, subjects
        adapter.run_migration()
        assert linked(adapter) == expected, subjects
    # The one who gave a subject up cannot sign in with it any more; the one who got it can.
    browser, response = oidc_contract.round_trip(adapter, sso, "oidc", legacy=True)
    assert location(response) == adapter.home and adapter.who(browser) == "bob"


def test_22_the_old_settings_are_empty_while_the_entry_is_off_or_does_not_trust_the_provider(
    adapter: Any, sso: fakes.FakeProvider, oidc: Any
) -> None:
    """The way back writes the entry exactly, or nothing: the version before the list knows neither switch, and would
    let a person in without the second factor the new version asks."""
    if not _one_provider(adapter):
        return
    migrated(adapter, sso)
    full = {
        "issuer": sso.issuer.rstrip("/"),
        "client_id": sso.client_id,
        "client_secret": sso.client_secret,
        "label": "Company",
    }

    def settings() -> dict[str, str]:
        read = adapter.legacy_read()
        return {key: read[key] for key in full}

    # An app whose older version asked the code (nexdiary, nexpaper) migrated to an entry that does not trust the
    # provider; trusted, its settings are written in full.
    change_entry(adapter, oidc, trusts_second_factor=True)
    assert settings() == full and adapter.legacy_read()["subjects"] == {"alex": "person-1"}
    for values in (
        {"trusts_second_factor": False},
        {"enabled": False},
        {"enabled": False, "trusts_second_factor": True},
    ):
        change_entry(adapter, oidc, **values)
        for _ in range(2):
            assert not settings()["issuer"] or not settings()["client_id"], values
            adapter.run_migration()
        change_entry(adapter, oidc, enabled=True, trusts_second_factor=True)
        assert settings() == full, values


def test_22_up_again_a_provider_the_older_version_removed_is_gone(adapter: Any, sso: fakes.FakeProvider) -> None:
    if not _one_provider(adapter):
        return
    migrated(adapter, sso)
    older(adapter, sso, subjects={"alex": ""}, issuer="", client_id="", client_secret="", label="")
    adapter.run_migration()
    assert the_entry(adapter) is None
    assert oidc_contract.error_in(start(adapter, adapter.browser(), "oidc")) == "oidc_not_configured"


def test_22_up_again_a_provider_the_older_version_set_up_comes_back_as_the_entry_oidc(
    adapter: Any, sso: fakes.FakeProvider
) -> None:
    if not _one_provider(adapter):
        return
    migrated(adapter, sso)
    with adapter.store() as store:
        store.delete_provider(store.provider_by_slug("oidc").id)
        store.commit()
    adapter.run_migration()
    assert the_entry(adapter) is None
    older(adapter, sso, subjects={"alex": "person-1"}, label="Again")
    adapter.run_migration()
    entry = the_entry(adapter)
    assert entry is not None and (entry.label, entry.enabled, entry.redirect_path) == ("Again", True, "")
    assert linked(adapter) == {"alex": "person-1"}
    browser = adapter.browser()
    target = location(start(adapter, browser, "oidc"))
    assert fakes.query_of(target)["redirect_uri"].endswith(adapter.path("legacy_callback"))
    response = oidc_contract.come_back(adapter, browser, "oidc", sso.authorize(target), legacy=True)
    assert location(response) == adapter.home and adapter.who(browser) == "alex"


def test_22_up_again_new_values_for_the_same_issuer_are_taken_over_and_the_links_stay(
    adapter: Any, sso: fakes.FakeProvider
) -> None:
    if not _one_provider(adapter):
        return
    migrated(adapter, sso)
    older(
        adapter,
        sso,
        subjects={"alex": "person-1"},
        issuer=sso.issuer + "/",
        client_id="client-of-the-older-version",
        client_secret="secret-of-the-older-version",
        label="Renamed",
    )
    adapter.run_migration()
    entry = the_entry(adapter)
    assert (entry.issuer, entry.label, entry.client_id, entry.client_secret) == (
        sso.issuer,
        "Renamed",
        "client-of-the-older-version",
        "secret-of-the-older-version",
    )
    assert linked(adapter) == {"alex": "person-1"}


def test_22_up_again_another_issuer_drops_the_links_and_takes_the_values(adapter: Any, sso: fakes.FakeProvider) -> None:
    if not _one_provider(adapter):
        return
    migrated(adapter, sso)
    other = "https://other.example.com"
    # alex's subject stands unchanged: it was the old issuer's, and goes with it.
    older(adapter, sso, subjects={"alex": "person-1"}, issuer=other, client_id="other-client", label="Other")
    adapter.run_migration()
    entry = the_entry(adapter)
    assert (entry.issuer, entry.client_id, entry.label) == (other, "other-client", "Other")
    assert linked(adapter) == {}
    older(adapter, sso, subjects={"alex": "other-7"}, issuer=other, client_id="other-client", label="Other")
    adapter.run_migration()
    assert linked(adapter) == {"alex": "other-7"}


def test_22_up_again_a_provider_set_up_while_the_entry_was_off_turns_it_on(
    adapter: Any, sso: fakes.FakeProvider, oidc: Any
) -> None:
    if not _one_provider(adapter):
        return
    migrated(adapter, sso)
    change_entry(adapter, oidc, enabled=False)
    adapter.run_migration()
    older(adapter, sso, subjects={"alex": "person-1"}, label="On again")
    adapter.run_migration()
    entry = the_entry(adapter)
    assert (entry.enabled, entry.label, entry.trusts_second_factor) == (True, "On again", adapter.trusted_before)
    assert linked(adapter) == {"alex": "person-1"}


def test_22_up_again_nothing_is_taken_over_while_coupled(adapter: Any, sso: fakes.FakeProvider, oidc: Any) -> None:
    if not _one_provider(adapter) or not adapter.supports_coupling:
        return
    ids = migrated(adapter, sso)
    suite = fakes.FakeProvider("https://suite.example.com")
    with adapter.store() as store:
        oidc.coupling.couple(
            store,
            issuer=suite.issuer,
            client_id=suite.client_id,
            client_secret=suite.client_secret,
            label="Suite",
            people=[(ids["alex"], "p-alex")],
        )
    adapter.run_migration()
    before = sorted((item["slug"], item["issuer"], item["enabled"]) for item in adapter.providers())
    older(adapter, sso, subjects={"alex": "elsewhere"}, issuer="https://other.example.com", client_id="other")
    adapter.run_migration()
    assert sorted((item["slug"], item["issuer"], item["enabled"]) for item in adapter.providers()) == before


def test_22_the_helper_for_entries_in_the_store_is_untouched_by_the_way_back(adapter: Any, net: fakes.Network) -> None:
    """Another entry than ``oidc`` is no business of the way back: a start leaves it alone."""
    if not _one_provider(adapter):
        return
    fake = net.add(fakes.FakeProvider("https://store.example.com"))
    add_in_store(adapter, fake, "stored")
    adapter.run_migration()
    adapter.run_migration()
    with adapter.store() as store:
        assert [item.slug for item in store.list_providers()] == ["stored"]

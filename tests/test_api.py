"""The HTTP surface: tenants are reached by name and user, and a missing and a forbidden tenant look the same."""

import pytest
from fastapi.testclient import TestClient

from dclabaccount import saft
from dclabaccount.api import create_app


@pytest.fixture
def client(router):
    return TestClient(create_app(router))


def headers(corpus, user):
    return {"X-User": str(corpus.users[user])}


def test_a_tenant_query_answers_for_a_member_and_never_shows_another_tenants_rows(client, corpus) -> None:
    response = client.get("/t/alfa-lda/account_balances", headers=headers(corpus, "staff-alfa-lda"))
    assert response.status_code == 200 and {r["tenant_id"] for r in response.json()} == {str(corpus.tenants["alfa-lda"]["id"])}


def test_a_missing_and_a_forbidden_tenant_get_the_same_answer(client, corpus) -> None:
    forbidden = client.get("/t/beta-sa/accounts", headers=headers(corpus, "staff-alfa-lda"))
    missing = client.get("/t/no-such-tenant/accounts", headers=headers(corpus, "staff-alfa-lda"))
    assert (forbidden.status_code, forbidden.json()) == (missing.status_code, missing.json()) == (404, {"detail": "tenant not found"})
    assert client.get("/t/alfa-lda/accounts").status_code == 401


def test_the_unfiltered_query_is_not_exposed_over_http(client, corpus) -> None:
    assert client.get("/t/alfa-lda/forgot_the_filter", headers=headers(corpus, "staff-alfa-lda")).status_code == 404


def test_uploading_a_file_imports_or_quarantines_it(client, corpus) -> None:
    tenant = corpus.tenants["alfa-lda"]
    good = saft.generate(tenant["name"], tenant["nif"], 2025, 3, 20)
    sent = client.post("/t/alfa-lda/imports", content=good, headers={**headers(corpus, "staff-alfa-lda"), "X-Filename": "2025-03.xml"})
    assert sent.json()["status"] == "accepted" and sent.json()["lines"] == 40
    bad = client.post("/t/alfa-lda/imports", content=saft.corrupt(good, "unbalanced"), headers=headers(corpus, "staff-alfa-lda"))
    assert bad.json()["status"] == "quarantined" and bad.json()["problems"]


def test_my_work_is_the_one_cross_tenant_endpoint(client, corpus) -> None:
    rita = client.get("/me/work", headers=headers(corpus, "rita")).json()
    assert {r["tenant"] for r in rita} == set(corpus.tenants)
    assert {r["tenant"] for r in client.get("/me/work", headers=headers(corpus, "staff-alfa-lda")).json()} == {"alfa-lda"}

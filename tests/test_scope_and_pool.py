"""The cross-tenant exception, kept like code, and the pooled-connection leak test."""

import re
from pathlib import Path

import pytest
import yaml

from dclabaccount import exceptions
from dclabaccount.db import Router, admin_connection

SRC = Path(__file__).resolve().parent.parent / "src" / "dclabaccount"


def test_an_accountant_sees_their_work_across_all_their_clients_in_one_result(router, corpus) -> None:
    rows = exceptions.run(router, corpus.users["rita"], "my_work_across_clients")
    assert {r["tenant"] for r in rows} == set(corpus.tenants)  # five clients, deadlines of each
    assert {r["tenant"] for r in rows if r["item"] == "import"} == {"alfa-lda", "beta-sa"}
    paulo = exceptions.run(router, corpus.users["paulo"], "my_work_across_clients")
    assert {r["tenant"] for r in paulo} == {"alfa-lda", "beta-sa"}  # only the clients Paulo works for
    assert exceptions.run(router, corpus.users["staff-gama-unip"], "my_work_across_clients")
    assert {r["tenant"] for r in exceptions.run(router, corpus.users["staff-gama-unip"], "my_work_across_clients")} == {"gama-unip"}


def test_the_scope_exists_only_inside_its_transaction(router, corpus) -> None:
    """Entering a scope, then asking for the same tables in the next transaction on the same connection, gives no rows."""
    pool = router.pools["shared"]
    with pool.connection() as conn:
        conn.execute("SELECT app.enter_scope(%s, 'my_work_across_clients')", (corpus.users["rita"],))
        assert conn.execute("SELECT count(*) AS n FROM filing_deadline").fetchone()["n"] == 20
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM filing_deadline").fetchone()["n"] == 0


def test_an_exception_that_is_not_on_the_reviewed_list_is_refused_by_the_code_and_by_the_database(router, corpus) -> None:
    with pytest.raises(exceptions.ExceptionNotReviewed):
        exceptions.run(router, corpus.users["rita"], "everything")
    with pytest.raises(Exception, match="not on the reviewed list"):
        with router.pools["shared"].connection() as conn:
            conn.execute("SELECT app.enter_scope(%s, 'everything')", (corpus.users["rita"],))


def test_a_changed_statement_needs_a_new_review(router, corpus) -> None:
    entries = exceptions.load()
    entry = entries["my_work_across_clients"]
    tampered = {**entries, entry.id: exceptions.ReviewedException(entry.id, entry.description, entry.sql + "\n-- one more column", entry.sql_sha256, entry.reviewer, entry.reviewed_on)}
    with pytest.raises(exceptions.ExceptionNotReviewed, match="changed since"):
        exceptions.run(router, corpus.users["rita"], entry.id, entries=tampered)
    reformatted = exceptions.ReviewedException(entry.id, entry.description, entry.sql.replace("\n", "\n  "), entry.sql_sha256, entry.reviewer, entry.reviewed_on)
    assert reformatted.actual_hash == entry.sql_sha256  # whitespace is not a change


def test_the_reviewed_list_and_the_database_agree_and_every_use_is_recorded(router, corpus) -> None:
    listed = yaml.safe_load(exceptions.LIST.read_text())["exceptions"]
    with admin_connection("shared") as admin:
        stored = {r["id"]: r["sql_sha256"] for r in admin.execute("SELECT id, sql_sha256 FROM app.reviewed_exception")}
        before = admin.execute("SELECT count(*) AS n FROM app.exception_use").fetchone()["n"]
        exceptions.run(router, corpus.users["rita"], "my_work_across_clients")
        uses = admin.execute("SELECT exception_id, tenants FROM app.exception_use ORDER BY id DESC LIMIT 1").fetchone()
        after = admin.execute("SELECT count(*) AS n FROM app.exception_use").fetchone()["n"]
    assert stored == {e["id"]: e["sql_sha256"] for e in listed}
    assert after == before + 1 and uses == {"exception_id": "my_work_across_clients", "tenants": 5}


def test_nothing_but_the_exception_module_enters_a_scope() -> None:
    callers = [p.name for p in SRC.glob("*.py") if re.search(r"enter_scope\(", p.read_text()) and p.name != "exceptions.py"]
    assert callers == []


def test_the_pooled_connection_does_not_carry_a_tenant_to_the_next_request(corpus) -> None:
    """One connection in the pool, two requests: the second forgets to name a tenant and must see nothing."""
    one = Router(pool_size=1)
    try:
        with one.tenant_tx("alfa-lda", corpus.users["staff-alfa-lda"], store="ledger") as conn:
            assert conn.execute("SELECT count(*) AS n FROM ledger_line").fetchone()["n"] == 400
        with one.pools["shared"].connection() as conn:  # the same connection, no tenant set
            assert conn.execute("SELECT current_setting('app.tenant_id', true) AS t").fetchone()["t"] in (None, "")
            assert conn.execute("SELECT count(*) AS n FROM ledger_line").fetchone()["n"] == 0
    finally:
        one.close()


def test_the_leak_test_fails_for_a_session_level_setting(corpus) -> None:
    """The negative control: if the tenant were set for the session, the next request would run as the previous tenant."""
    one = Router(pool_size=1)
    try:
        with one.tenant_tx("alfa-lda", corpus.users["staff-alfa-lda"], store="ledger", _unsafe_session_scope=True) as conn:
            conn.execute("SELECT 1")
        with one.pools["shared"].connection() as conn:
            assert conn.execute("SELECT count(*) AS n FROM ledger_line").fetchone()["n"] == 400  # alfa's rows: a leak
    finally:
        one.close()

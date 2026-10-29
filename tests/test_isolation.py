"""Row-level security, as the application uses it: two tenants, the same queries, never a foreign row."""

import pytest

from dclabaccount import generator
from dclabaccount.db import NotFound, admin_connection
from dclabaccount.queries import QUERIES


def run_as(router, corpus, tenant: str, user: str, query):
    with router.tenant_tx(tenant, corpus.users[user], store=query.store) as conn:
        return query.run(conn)


def test_the_application_role_cannot_bypass_row_level_security(router, corpus) -> None:
    with router.pools["shared"].connection() as conn:
        role = conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
        owned = conn.execute("SELECT count(*) AS n FROM pg_tables WHERE tableowner = current_user").fetchone()["n"]
        unprotected = conn.execute(
            "SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version' "
            "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
        ).fetchone()["n"]
    assert (role["rolsuper"], role["rolbypassrls"], owned, unprotected) == (False, False, 0, 0)


def test_every_registered_query_returns_only_the_callers_rows_for_two_tenants(router, corpus) -> None:
    assert len(QUERIES) >= 7
    for query in QUERIES.values():
        for tenant, user in (("alfa-lda", "staff-alfa-lda"), ("beta-sa", "staff-beta-sa")):
            rows = run_as(router, corpus, tenant, user, query)
            assert rows, f"{query.name} returned nothing for {tenant}: the test would pass for the wrong reason"
            foreign = [r for r in rows if r["tenant_id"] != corpus.tenants[tenant]["id"]]
            assert not foreign, f"{query.name} leaked {len(foreign)} row(s) of another tenant to {tenant}"


def test_the_two_tenant_test_fails_when_row_level_security_is_off(router, corpus) -> None:
    """A test that cannot fail proves nothing: switch the policy off on one table and the same walk must catch it."""
    with admin_connection("shared") as admin:
        admin.execute("ALTER TABLE ledger_line DISABLE ROW LEVEL SECURITY")
        try:
            leaks = []
            for query in QUERIES.values():
                if query.store != "ledger":
                    continue
                rows = run_as(router, corpus, "alfa-lda", "staff-alfa-lda", query)
                leaks += [r for r in rows if r["tenant_id"] != corpus.tenants["alfa-lda"]["id"]]
            assert leaks
        finally:
            admin.execute("ALTER TABLE ledger_line ENABLE ROW LEVEL SECURITY")


def test_a_write_for_another_tenant_is_refused(router, corpus) -> None:
    other = corpus.tenants["beta-sa"]["id"]
    with pytest.raises(Exception, match="row-level security"):
        with router.tenant_tx("alfa-lda", corpus.users["staff-alfa-lda"]) as conn:
            conn.execute("INSERT INTO account (tenant_id, code, name) VALUES (%s, '99', 'planted')", (other,))


def test_a_user_cannot_enter_a_tenant_they_do_not_belong_to(router, corpus) -> None:
    with pytest.raises(NotFound):
        router.resolve("beta-sa", corpus.users["staff-alfa-lda"])
    with pytest.raises(NotFound):
        router.resolve("no-such-tenant", corpus.users["rita"])


def test_the_application_role_cannot_read_the_control_tables(router, corpus) -> None:
    with pytest.raises(Exception, match="permission denied"):
        with router.pools["shared"].connection() as conn:
            conn.execute("SELECT * FROM app.membership")


def test_generated_company_numbers_have_valid_checksums(corpus) -> None:
    assert all(generator.valid_nif(t["nif"]) for t in corpus.tenants.values())
    assert not generator.valid_nif("500000001") and generator.valid_nif(generator.make_nif(1))

"""A tenant's ledger lines move between databases, and a tenant is offboarded with an export and an erasure."""

from datetime import date, timedelta

import pytest

from dclabaccount import move, offboard
from dclabaccount.db import admin_connection
from dclabaccount.queries import QUERIES


def balances(router, corpus, slug="bigco"):
    with router.tenant_tx(slug, corpus.users[f"staff-{slug}"], store="ledger") as conn:
        return [dict(r) for r in QUERIES["account_balances"].run(conn)]


def count(database, tenant_id) -> int:
    with admin_connection(database) as conn:
        return conn.execute("SELECT count(*) AS n FROM ledger_line WHERE tenant_id = %s", (tenant_id,)).fetchone()["n"]


def test_a_tenants_lines_move_and_reads_do_not_change(router, corpus) -> None:
    before, tenant = balances(router, corpus), corpus.tenants["bigco"]["id"]
    report = move.move_stream(router, corpus.users["staff-bigco"], "bigco", "heavy")
    assert (report.lines, report.verified, report.switched) == (2000, True, True)
    assert balances(router, corpus) == before
    assert (count("shared", tenant), count("heavy", tenant)) == (2000, 2000)  # the old rows stay until retired
    others = balances(router, corpus, "alfa-lda")
    assert others and sum(r["lines"] for r in others) == 400  # a quiet tenant is untouched


def test_going_back_before_the_old_rows_are_deleted_is_one_routing_change(router, corpus) -> None:
    before = balances(router, corpus)
    move.move_stream(router, corpus.users["staff-bigco"], "bigco", "heavy")
    move.switch("bigco", None)
    assert balances(router, corpus) == before
    again = move.move_stream(router, corpus.users["staff-bigco"], "bigco", "heavy")  # clears the target first
    assert again.lines == 2000 and count("heavy", corpus.tenants["bigco"]["id"]) == 2000


def test_the_old_rows_are_deleted_only_after_a_verified_switch(router, corpus) -> None:
    user, tenant = corpus.users["staff-bigco"], corpus.tenants["bigco"]["id"]
    with pytest.raises(move.MoveRefused, match="still points"):
        move.retire(router, user, "bigco", "shared")
    move.move_stream(router, user, "bigco", "heavy")
    with admin_connection("heavy") as heavy:  # damage the target: the move must no longer verify
        heavy.execute("DELETE FROM ledger_line WHERE id = (SELECT min(id) FROM ledger_line WHERE tenant_id = %s)", (tenant,))
    with pytest.raises(move.MoveRefused, match="no longer matches"):
        move.retire(router, user, "bigco", "shared")
    assert count("shared", tenant) == 2000
    move.copy_stream(router, router.resolve("bigco", user), "shared", "heavy")  # repair
    assert move.retire(router, user, "bigco", "shared") == 2000 and count("shared", tenant) == 0


def test_a_copy_that_does_not_match_does_not_switch(router, corpus, monkeypatch) -> None:
    monkeypatch.setattr(move, "verify", lambda *a, **k: (False, {"source": 1, "target": 2}))
    with pytest.raises(move.MoveRefused, match="not switching"):
        move.move_stream(router, corpus.users["staff-bigco"], "bigco", "heavy")
    assert router.resolve("bigco", corpus.users["staff-bigco"]).database_key is None


def test_new_lines_after_the_move_get_ids_after_the_copied_ones(router, corpus) -> None:
    move.move_stream(router, corpus.users["staff-bigco"], "bigco", "heavy")
    tenant = corpus.tenants["bigco"]["id"]
    with router.tenant_tx("bigco", corpus.users["staff-bigco"], store="ledger") as conn:
        new = conn.execute(
            "INSERT INTO ledger_line (tenant_id, journal, txn_id, line_no, date, account_code, debit) VALUES (%s, 'X', 'x', 1, '2025-01-01', '11', 1) RETURNING id",
            (tenant,),
        ).fetchone()["id"]
    assert new > 2000


@pytest.fixture
def exported(router, corpus, tmp_path):
    offboard.export_tenant(router, corpus.users["staff-alfa-lda"], "alfa-lda", tmp_path)
    return tmp_path


def test_an_export_lists_every_table_with_counts_and_hashes_that_verify(router, corpus, exported) -> None:
    import json

    manifest = json.loads((exported / "alfa-lda" / "manifest.json").read_text())
    assert manifest["files"]["ledger_line"]["rows"] == 400 and manifest["files"]["account"]["rows"] == 10
    assert offboard.verify_export(exported, "alfa-lda")
    (exported / "alfa-lda" / "party.csv").write_text("tampered")
    assert not offboard.verify_export(exported, "alfa-lda")


def test_erasure_needs_a_verified_export(router, corpus, tmp_path) -> None:
    with pytest.raises(offboard.OffboardRefused, match="verified export"):
        offboard.erase(router, corpus.users["staff-alfa-lda"], "alfa-lda", tmp_path)


def test_erasure_removes_personal_data_and_keeps_the_accounting_records(router, corpus, exported) -> None:
    report = offboard.erase(router, corpus.users["staff-alfa-lda"], "alfa-lda", exported)
    assert report.erased_parties == 20 and report.deleted_quarantine_files == 0 and report.retention_until is None
    assert report.retained["ledger_line@shared"] == 400 and report.retained["account"] == 10
    assert "retention period not configured" in report.notes[0]
    with admin_connection("shared") as admin:
        parties = admin.execute("SELECT DISTINCT name, email, tax_id FROM party WHERE tenant_id = %s", (corpus.tenants["alfa-lda"]["id"],)).fetchall()
    assert parties == [{"name": "Erased", "email": None, "tax_id": None}]
    from dclabaccount.db import NotFound

    with pytest.raises(NotFound):  # nobody can enter an offboarded tenant
        router.resolve("alfa-lda", corpus.users["staff-alfa-lda"])


def test_the_records_cannot_be_purged_until_a_retention_date_is_set_and_passed(router, corpus, exported) -> None:
    offboard.erase(router, corpus.users["staff-alfa-lda"], "alfa-lda", exported)
    with pytest.raises(offboard.OffboardRefused, match="not configured"):
        offboard.purge("alfa-lda")
    offboard.set_retention("alfa-lda", date(2040, 1, 1))  # a made-up date for the test, not a legal period
    with pytest.raises(offboard.OffboardRefused, match="must be kept until 2040-01-01"):
        offboard.purge("alfa-lda", today=date(2039, 12, 31))
    deleted = offboard.purge("alfa-lda", today=date(2040, 1, 1) + timedelta(days=0))
    assert deleted["ledger_line"] == 400 and deleted["account"] == 10
    with admin_connection("shared") as admin:
        assert admin.execute("SELECT count(*) AS n FROM app.tenant WHERE slug = 'alfa-lda'").fetchone()["n"] == 0


def test_a_dedicated_tenant_is_exported_from_and_purged_in_its_own_database(router, corpus, tmp_path) -> None:
    user, tenant = corpus.users["staff-bigco"], corpus.tenants["bigco"]["id"]
    move.move_stream(router, user, "bigco", "heavy")
    move.retire(router, user, "bigco", "shared")
    manifest = offboard.export_tenant(router, user, "bigco", tmp_path)
    assert manifest["files"]["ledger_line"]["rows"] == 2000
    offboard.erase(router, user, "bigco", tmp_path)
    offboard.set_retention("bigco", date(2030, 1, 1))
    deleted = offboard.purge("bigco", today=date(2030, 1, 2))
    assert deleted["ledger_line@heavy"] == 2000 and count("heavy", tenant) == 0

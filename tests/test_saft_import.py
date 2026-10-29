"""SAF-T import: a good file, every way a file can be bad, and re-import."""

import pytest

from dclabaccount import generator, saft
from dclabaccount.db import admin_connection
from dclabaccount.imports import import_saft


def make(corpus, slug="alfa-lda", month=3, transactions=40, seed=1) -> bytes:
    tenant = corpus.tenants[slug]
    return saft.generate(tenant["name"], tenant["nif"], 2025, month, transactions, seed=seed)


def ledger_count(router, corpus, slug="alfa-lda", period_prefix="2025-03") -> int:
    with router.tenant_tx(slug, corpus.users[f"staff-{slug}"], store="ledger") as conn:
        return conn.execute("SELECT count(*) AS n FROM ledger_line WHERE to_char(date, 'YYYY-MM') = %s AND batch_id IS NOT NULL", (period_prefix,)).fetchone()["n"]


def batches(router, corpus, slug="alfa-lda"):
    with router.tenant_tx(slug, corpus.users[f"staff-{slug}"]) as conn:
        return conn.execute("SELECT status, period, line_count FROM import_batch WHERE filename <> 'seed.xml' AND filename <> 'bad.xml' ORDER BY created_at").fetchall()


def test_a_generated_file_passes_every_rule() -> None:
    nif = generator.make_nif(1)
    parsed, problems = saft.check(saft.generate("Alfa Exemplo, Lda.", nif, 2025, 3, 40), nif=nif)
    assert problems == [] and parsed.period == "2025-03" and parsed.lines == 80 and len(parsed.accounts) == 10


def test_a_good_file_enters_the_ledger_whole(router, corpus) -> None:
    before = ledger_count(router, corpus)
    result = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "2025-03.xml", make(corpus))
    assert (result.status, result.period, result.lines) == ("accepted", "2025-03", 80)
    assert ledger_count(router, corpus) == before + 80
    assert batches(router, corpus) == [{"status": "accepted", "period": "2025-03", "line_count": 80}]


def test_importing_the_same_file_again_changes_nothing(router, corpus) -> None:
    content = make(corpus)
    first = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "2025-03.xml", content)
    again = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "2025-03-again.xml", content)
    assert (first.status, again.status) == ("accepted", "unchanged")
    assert ledger_count(router, corpus) == 80 and len(batches(router, corpus)) == 1


def test_a_corrected_file_replaces_the_period_instead_of_adding_to_it(router, corpus) -> None:
    import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "2025-03.xml", make(corpus, transactions=40))
    corrected = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "2025-03-v2.xml", make(corpus, transactions=55, seed=2))
    assert (corrected.status, corrected.lines, corrected.replaced_lines) == ("accepted", 110, 80)
    assert ledger_count(router, corpus) == 110  # not 190
    assert [b["status"] for b in batches(router, corpus)] == ["superseded", "accepted"]
    other_month = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "2025-04.xml", make(corpus, month=4))
    assert other_month.status == "accepted" and ledger_count(router, corpus) == 110  # April does not touch March


@pytest.mark.parametrize("kind,code", sorted(saft.CORRUPTIONS.items()))
def test_a_bad_file_is_quarantined_with_its_reason_and_nothing_enters_the_ledger(router, corpus, kind, code) -> None:
    before = ledger_count(router, corpus)
    result = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", f"{kind}.xml", saft.corrupt(make(corpus, transactions=60), kind))
    assert result.status == "quarantined" and code in {p.code for p in result.problems}, [p.code for p in result.problems]
    assert ledger_count(router, corpus) == before
    with admin_connection("shared") as admin:
        stored = admin.execute("SELECT b.reasons, length(q.content) AS size FROM import_batch b JOIN quarantine_file q ON q.batch_id = b.id WHERE b.filename = %s", (f"{kind}.xml",)).fetchone()
    assert stored["size"] > 0 and code in {r["code"] for r in stored["reasons"]}


def test_a_quarantined_period_can_be_imported_once_the_file_is_fixed(router, corpus) -> None:
    bad = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "bad.xml", saft.corrupt(make(corpus), "unbalanced"))
    good = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "fixed.xml", make(corpus))
    assert (bad.status, good.status) == ("quarantined", "accepted") and ledger_count(router, corpus) == 80


def test_another_tenants_file_is_quarantined(router, corpus) -> None:
    beta = saft.generate("Beta Exemplo, S.A.", corpus.tenants["beta-sa"]["nif"], 2025, 3, 20)
    result = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "beta.xml", beta)
    assert result.status == "quarantined" and "WRONG_TAXPAYER" in {p.code for p in result.problems}


def test_the_period_a_caller_expects_is_checked() -> None:
    nif = generator.make_nif(1)
    _, problems = saft.check(saft.generate("Alfa", nif, 2025, 3, 10), nif=nif, expected_period="2025-04")
    assert {p.code for p in problems} == {"WRONG_PERIOD"}


def test_a_dedicated_tenants_lines_go_to_its_own_database(router) -> None:
    specs = tuple(generator.TenantSpec(s.slug, s.name, s.lines, dedicated=(s.slug == "bigco")) for s in generator.TEST_SPECS)
    corpus = generator.seed(specs)
    tenant = corpus.tenants["bigco"]
    result = import_saft(router, corpus.users["staff-bigco"], "bigco", "2025-03.xml", saft.generate(tenant["name"], tenant["nif"], 2025, 3, 30))
    assert result.status == "accepted"
    with admin_connection("heavy") as heavy, admin_connection("shared") as shared:
        in_heavy = heavy.execute("SELECT count(*) AS n FROM ledger_line WHERE batch_id = %s", (result.batch_id,)).fetchone()["n"]
        in_shared = shared.execute("SELECT count(*) AS n FROM ledger_line WHERE batch_id = %s", (result.batch_id,)).fetchone()["n"]
    assert (in_heavy, in_shared) == (60, 0)

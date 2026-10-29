"""A fictional corpus: companies with valid-checksum identifiers, a small chart of accounts, parties, deadlines and
ledger lines. Everything is invented. Company names say so ("Exemplo"), e-mail addresses use the reserved
``example.test`` domain, and the lab refuses to take a real SAF-T file (saft.py checks the watermark).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from dclabaccount.db import admin_connection

WATERMARK = "SYNTHETIC DATA: DcLabAccount lab, not a real company"
YEAR = 2025
CHART = (
    ("11", "Caixa"), ("12", "Depósitos à ordem"), ("21", "Clientes"), ("22", "Fornecedores"), ("24", "Estado e outros entes públicos"),
    ("31", "Compras"), ("62", "Fornecimentos e serviços externos"), ("63", "Gastos com o pessoal"), ("71", "Vendas"),
    ("72", "Prestações de serviços"),
)
ACCOUNT_CODES = [code for code, _ in CHART]


def nif_check_digit(first8: str) -> int:
    total = sum(int(digit) * weight for digit, weight in zip(first8, range(9, 1, -1), strict=True))
    digit = 11 - total % 11
    return 0 if digit >= 10 else digit


def make_nif(index: int, prefix: str = "5") -> str:
    """A syntactically valid Portuguese company tax number (starts with 5), made up from an index."""
    first8 = f"{prefix}{(index * 7919 + 10007) % 10_000_000:07d}"
    return first8 + str(nif_check_digit(first8))


def valid_nif(value: str) -> bool:
    return len(value) == 9 and value.isdigit() and int(value[8]) == nif_check_digit(value[:8])


@dataclass(frozen=True)
class TenantSpec:
    slug: str
    name: str
    lines: int
    dedicated: bool = False  # ledger lines seeded straight into the heavy database
    parties: int = 20


DEFAULT_SPECS = (
    TenantSpec("alfa-lda", "Alfa Exemplo, Lda.", 20_000),
    TenantSpec("beta-sa", "Beta Exemplo, S.A.", 20_000),
    TenantSpec("gama-unip", "Gama Exemplo, Unipessoal, Lda.", 20_000),
    TenantSpec("delta-sociedade", "Delta Exemplo, Sociedade", 20_000),
    TenantSpec("bigco", "BigCo Exemplo, S.A.", 3_000_000),
)
TEST_SPECS = tuple(TenantSpec(s.slug, s.name, 2_000 if s.slug == "bigco" else 400) for s in DEFAULT_SPECS)


@dataclass
class Corpus:
    tenants: dict[str, dict] = field(default_factory=dict)  # slug -> {id, nif, name}
    users: dict[str, UUID] = field(default_factory=dict)


LINES_SQL = """
INSERT INTO ledger_line (tenant_id, journal, txn_id, line_no, date, account_code, debit, credit, description)
SELECT %(tenant)s, 'GEN', 'T' || g, l.n, %(start)s::date + (g %% 365)::int,
       (%(codes)s::text[])[1 + (g * 7 + l.n * 3) %% %(ncodes)s],
       CASE WHEN l.n = 1 THEN round((50 + (g * 7919) %% 99950) / 100.0, 2) ELSE 0 END,
       CASE WHEN l.n = 2 THEN round((50 + (g * 7919) %% 99950) / 100.0, 2) ELSE 0 END,
       'synthetic'
FROM generate_series(1::bigint, %(txns)s::bigint) AS g CROSS JOIN (VALUES (1), (2)) AS l(n)
"""


def reset() -> None:
    """Empty both databases (operator action)."""
    for database in ("shared", "heavy"):
        with admin_connection(database) as conn:
            if database == "shared":
                conn.execute(
                    "TRUNCATE quarantine_file, import_batch, filing_deadline, party, account, ledger_line, "
                    "app.exception_use, app.scope_grant, app.membership, app.app_user, app.tenant RESTART IDENTITY CASCADE"
                )
            else:
                conn.execute("TRUNCATE ledger_line RESTART IDENTITY")


def seed(specs=DEFAULT_SPECS, *, progress=None) -> Corpus:
    reset()
    corpus = Corpus()
    with admin_connection("shared") as shared, admin_connection("heavy") as heavy:
        for index, spec in enumerate(specs, start=1):
            nif = make_nif(index)
            tenant_id = shared.execute(
                "INSERT INTO app.tenant (slug, name, nif, database_key) VALUES (%s, %s, %s, %s) RETURNING id",
                (spec.slug, spec.name, nif, "heavy" if spec.dedicated else None),
            ).fetchone()["id"]
            corpus.tenants[spec.slug] = {"id": tenant_id, "nif": nif, "name": spec.name}
            for code, name in CHART:
                shared.execute("INSERT INTO account (tenant_id, code, name) VALUES (%s, %s, %s)", (tenant_id, code, name))
            for n in range(1, spec.parties + 1):
                kind = "customer" if n % 2 else "supplier"
                shared.execute(
                    "INSERT INTO party (tenant_id, kind, name, email, tax_id, address) VALUES (%s, %s, %s, %s, %s, %s)",
                    (tenant_id, kind, f"{kind.title()} Exemplo {n:03d}", f"{kind[0]}{n:03d}@example.test", make_nif(index * 1000 + n, "2"),
                     f"Rua Exemplo {n}, Cidade Exemplo"),
                )
            for kind, due in (("VAT", date(YEAR + 1, 1, 15)), ("IRS", date(YEAR + 1, 5, 31)), ("IES", date(YEAR + 1, 7, 15)), ("VAT", date(YEAR + 1, 4, 15))):
                shared.execute("INSERT INTO filing_deadline (tenant_id, kind, due_date) VALUES (%s, %s, %s)", (tenant_id, kind, due))
            shared.execute(
                "INSERT INTO import_batch (tenant_id, filename, period, sha256, status, line_count) VALUES (%s, 'seed.xml', '2025-01', %s, 'accepted', 0)",
                (tenant_id, f"seed-{index}"),
            )
            if index <= 2:  # two tenants have a file waiting in quarantine, for the accountant's cross-client view
                shared.execute(
                    "INSERT INTO import_batch (tenant_id, filename, period, sha256, status, reasons) VALUES (%s, 'bad.xml', '2025-02', %s, 'quarantined', %s)",
                    (tenant_id, f"bad-{index}", '["unbalanced transaction"]'),
                )
            target = heavy if spec.dedicated else shared
            target.execute(
                LINES_SQL,
                {"tenant": tenant_id, "start": date(YEAR, 1, 1), "codes": ACCOUNT_CODES, "ncodes": len(ACCOUNT_CODES), "txns": spec.lines // 2},
            )
            if progress:
                progress(f"{spec.slug}: {spec.lines} lines")
        slugs = list(corpus.tenants)
        people = {"rita": slugs, "paulo": slugs[:2], **{f"staff-{s}": [s] for s in slugs}}
        for person, member_of in people.items():
            user_id = shared.execute("INSERT INTO app.app_user (name) VALUES (%s) RETURNING id", (person,)).fetchone()["id"]
            corpus.users[person] = user_id
            for slug in member_of:
                shared.execute(
                    "INSERT INTO app.membership (user_id, tenant_id, role) VALUES (%s, %s, %s)",
                    (user_id, corpus.tenants[slug]["id"], "accountant" if person in ("rita", "paulo") else "staff"),
                )
        shared.execute("ANALYZE")
        heavy.execute("ANALYZE")
    return corpus

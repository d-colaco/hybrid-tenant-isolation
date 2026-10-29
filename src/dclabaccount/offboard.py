"""Offboarding a tenant: export, then erase what must go, and keep the accounting records for their retention period.

The tension is real: a data subject may ask for erasure, and the law requires accounting records to be kept for a period
after which they must go. The lab does the part that can be done at once and refuses the rest until the period is set:

1. ``export_tenant`` writes every table to CSV with a manifest of row counts and hashes, and ``verify_export`` checks it.
2. ``erase`` refuses without a verified export. It then erases personal data (party names, e-mails, tax numbers and
   addresses become placeholders; the raw files in quarantine are deleted), marks the tenant offboarded so nobody can
   enter it, and **keeps** the ledger lines, accounts and deadlines.
3. ``purge`` deletes everything, in both databases, but only once ``retention_until`` has passed. The date is set by the
   operator (``set_retention``). **The lab states no period and defaults to none**: it is a legal figure to verify with the
   accountant before any value is written anywhere. The tests use a made-up date.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from dclabaccount.db import Router, admin_connection

TABLES = {
    "account": ("shared", "tenant_id, code, name FROM account ORDER BY code"),
    "party": ("shared", "tenant_id, id, kind, name, email, tax_id, address, erased FROM party ORDER BY name, id"),
    "filing_deadline": ("shared", "tenant_id, id, kind, due_date, status FROM filing_deadline ORDER BY due_date, id"),
    "import_batch": ("shared", "tenant_id, id, filename, period, sha256, status, reasons, line_count, created_at FROM import_batch ORDER BY created_at, id"),
    "ledger_line": ("ledger", "tenant_id, id, journal, txn_id, line_no, date, account_code, debit, credit, description, party_id, batch_id FROM ledger_line ORDER BY id"),
}


class OffboardRefused(Exception):
    pass


@dataclass
class OffboardReport:
    tenant: str
    erased_parties: int = 0
    deleted_quarantine_files: int = 0
    retained: dict[str, int] = field(default_factory=dict)
    retention_until: date | None = None
    notes: list[str] = field(default_factory=list)


def _csv(conn, select: str) -> tuple[bytes, int]:
    out = io.StringIO()
    writer = csv.writer(out)
    cursor = conn.execute(f"SELECT {select}")
    writer.writerow([c.name for c in cursor.description])
    rows = 0
    for row in cursor:
        writer.writerow(["" if v is None else (json.dumps(v) if isinstance(v, (list, dict)) else v) for v in row.values()])
        rows += 1
    return out.getvalue().encode(), rows


def export_tenant(router: Router, user_id: UUID, slug: str, dest: Path) -> dict:
    folder = dest / slug
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    for name, (store, select) in TABLES.items():
        with router.tenant_tx(slug, user_id, store=store) as conn:
            data, rows = _csv(conn, select)
        (folder / f"{name}.csv").write_bytes(data)
        files[name] = {"rows": rows, "sha256": hashlib.sha256(data).hexdigest()}
    manifest = {"tenant": slug, "exported_at": datetime.now(timezone.utc).isoformat(), "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def verify_export(dest: Path, slug: str) -> bool:
    folder = dest / slug
    try:
        manifest = json.loads((folder / "manifest.json").read_text())
        for name, meta in manifest["files"].items():
            data = (folder / f"{name}.csv").read_bytes()
            if hashlib.sha256(data).hexdigest() != meta["sha256"] or data.count(b"\n") < meta["rows"]:
                return False
        return set(manifest["files"]) == set(TABLES)
    except (OSError, KeyError, ValueError):
        return False


def set_retention(slug: str, until: date) -> None:
    """An operator action: the end of the retention period for this tenant's accounting records."""
    with admin_connection("shared") as admin:
        admin.execute("UPDATE app.tenant SET retention_until = %s WHERE slug = %s", (until, slug))


def erase(router: Router, user_id: UUID, slug: str, export_dir: Path) -> OffboardReport:
    if not verify_export(export_dir, slug):
        raise OffboardRefused("no verified export: export the tenant first and keep the files")
    report = OffboardReport(slug)
    with router.tenant_tx(slug, user_id) as conn:
        report.erased_parties = conn.execute(
            "UPDATE party SET name = 'Erased', email = NULL, tax_id = NULL, address = NULL, erased = true WHERE NOT erased"
        ).rowcount
        report.deleted_quarantine_files = conn.execute("DELETE FROM quarantine_file").rowcount
    with admin_connection("shared") as admin:
        retention = admin.execute("SELECT retention_until FROM app.tenant WHERE slug = %s", (slug,)).fetchone()["retention_until"]
        admin.execute("UPDATE app.tenant SET status = 'offboarded' WHERE slug = %s", (slug,))
    report.retention_until = retention
    report.retained = _retained_counts(slug)
    report.notes.append(
        "accounting records are kept until the retention date" if retention else
        "retention period not configured: the accounting records are kept and cannot be purged until the operator sets it"
    )
    return report


def _retained_counts(slug: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with admin_connection("shared") as shared:
        tenant = shared.execute("SELECT id, database_key FROM app.tenant WHERE slug = %s", (slug,)).fetchone()
        for table in ("account", "filing_deadline", "import_batch", "party"):
            counts[table] = shared.execute(f"SELECT count(*) AS n FROM {table} WHERE tenant_id = %s", (tenant["id"],)).fetchone()["n"]
    for database in ("shared", "heavy"):
        with admin_connection(database) as conn:
            counts[f"ledger_line@{database}"] = conn.execute("SELECT count(*) AS n FROM ledger_line WHERE tenant_id = %s", (tenant["id"],)).fetchone()["n"]
    return counts


def purge(slug: str, *, today: date | None = None) -> dict[str, int]:
    """Delete everything of an offboarded tenant, in both databases, once its retention period has passed."""
    today = today or date.today()
    with admin_connection("shared") as shared:
        tenant = shared.execute("SELECT id, status, retention_until FROM app.tenant WHERE slug = %s", (slug,)).fetchone()
        if tenant is None:
            raise OffboardRefused("no such tenant")
        if tenant["status"] != "offboarded":
            raise OffboardRefused("the tenant is not offboarded")
        if tenant["retention_until"] is None:
            raise OffboardRefused("the retention period is not configured; verify it, then set it")
        if today < tenant["retention_until"]:
            raise OffboardRefused(f"the accounting records must be kept until {tenant['retention_until']}")
        deleted: dict[str, int] = {}
        with admin_connection("heavy") as heavy:
            deleted["ledger_line@heavy"] = heavy.execute("DELETE FROM ledger_line WHERE tenant_id = %s", (tenant["id"],)).rowcount
        for table in ("quarantine_file", "import_batch", "filing_deadline", "party", "account", "ledger_line"):
            deleted[table] = shared.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant["id"],)).rowcount
        shared.execute("DELETE FROM app.scope_grant WHERE tenant_id = %s", (tenant["id"],))
        shared.execute("DELETE FROM app.membership WHERE tenant_id = %s", (tenant["id"],))
        shared.execute("DELETE FROM app.tenant WHERE id = %s", (tenant["id"],))
        return deleted

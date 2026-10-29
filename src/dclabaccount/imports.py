"""SAF-T import: validate, quarantine what fails, replace what was imported before.

* A file that fails any rule is **quarantined**: recorded with its reasons and its bytes, and nothing of it reaches the
  ledger. The ledger only ever sees a whole file that passed.
* Importing the same file again changes nothing. Importing a corrected file for a period that was already imported
  **replaces** that period's lines (the earlier batch is marked superseded); it never appends a second copy.
* The ledger lines go to the database the tenant's routing names; accounts, parties and batches stay in the shared one.
  The two writes are not one transaction. A failure between them leaves the batch marked ``failed`` and its lines
  removed, so running the import again repairs it.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from itertools import islice
from uuid import UUID, uuid4

from dclabaccount import saft
from dclabaccount.db import Router

BATCH = 5000
COLUMNS = "(tenant_id, journal, txn_id, line_no, date, account_code, debit, credit, description, batch_id)"


@dataclass
class ImportResult:
    status: str  # accepted, unchanged, quarantined, failed
    period: str | None = None
    batch_id: UUID | None = None
    lines: int = 0
    replaced_lines: int = 0
    problems: list[saft.Problem] = field(default_factory=list)
    seconds: float = 0.0


def import_saft(router: Router, user_id: UUID, slug: str, filename: str, content: bytes) -> ImportResult:
    started = time.perf_counter()
    ref = router.resolve(slug, user_id)
    sha = hashlib.sha256(content).hexdigest()
    parsed, problems = saft.check(content, nif=ref.nif)
    if problems or parsed is None:
        return _quarantine(router, user_id, slug, filename, content, sha, parsed.period if parsed else None, problems, started)

    period = parsed.period or ""
    with router.tenant_tx(slug, user_id) as conn:
        previous = conn.execute("SELECT id, sha256 FROM import_batch WHERE period = %s AND status = 'accepted'", (period,)).fetchall()
    if any(row["sha256"] == sha for row in previous):
        return ImportResult("unchanged", period, lines=parsed.lines, seconds=time.perf_counter() - started)

    batch_id = uuid4()
    with router.tenant_tx(slug, user_id) as conn:
        for code, name in parsed.accounts.items():
            conn.execute(
                "INSERT INTO account (tenant_id, code, name) VALUES (%s, %s, %s) ON CONFLICT (tenant_id, code) DO UPDATE SET name = EXCLUDED.name",
                (ref.id, code, name),
            )
        for party in parsed.parties:
            known = conn.execute("SELECT id FROM party WHERE tax_id = %s", (party["tax_id"],)).fetchone()
            if known is None:
                conn.execute(
                    "INSERT INTO party (tenant_id, kind, name, email, tax_id) VALUES (%s, %s, %s, %s, %s)",
                    (ref.id, party["kind"], party["name"], party["email"], party["tax_id"]),
                )
        conn.execute(
            "INSERT INTO import_batch (tenant_id, id, filename, period, sha256, status) VALUES (%s, %s, %s, %s, %s, 'importing')",
            (ref.id, batch_id, filename, period, sha),
        )
    replaced = 0
    try:
        with router.tenant_tx(slug, user_id, store="ledger") as conn:
            for row in previous:
                replaced += conn.execute("DELETE FROM ledger_line WHERE batch_id = %s", (row["id"],)).rowcount
            # COPY FROM is refused on a table with row-level security, so the lines go in as batched INSERTs
            rows = (
                (ref.id, txn.journal, txn.id, number, txn.day, line.account, line.debit, line.credit, line.description, batch_id)
                for txn in parsed.transactions
                for number, line in enumerate(txn.lines, start=1)
            )
            with conn.cursor() as cur:
                while chunk := list(islice(rows, BATCH)):
                    cur.executemany(f"INSERT INTO ledger_line {COLUMNS} VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", chunk)
    except Exception:
        with router.tenant_tx(slug, user_id, store="ledger") as conn:
            conn.execute("DELETE FROM ledger_line WHERE batch_id = %s", (batch_id,))
        with router.tenant_tx(slug, user_id) as conn:
            conn.execute("UPDATE import_batch SET status = 'failed' WHERE id = %s", (batch_id,))
        raise
    with router.tenant_tx(slug, user_id) as conn:
        for row in previous:
            conn.execute("UPDATE import_batch SET status = 'superseded' WHERE id = %s", (row["id"],))
        conn.execute("UPDATE import_batch SET status = 'accepted', line_count = %s WHERE id = %s", (parsed.lines, batch_id))
    return ImportResult("accepted", period, batch_id, parsed.lines, replaced, seconds=time.perf_counter() - started)


def _quarantine(router, user_id, slug, filename, content, sha, period, problems, started) -> ImportResult:
    ref = router.resolve(slug, user_id)
    batch_id = uuid4()
    reasons = json.dumps([{"code": p.code, "message": p.message} for p in problems])
    with router.tenant_tx(slug, user_id) as conn:
        conn.execute(
            "INSERT INTO import_batch (tenant_id, id, filename, period, sha256, status, reasons) VALUES (%s, %s, %s, %s, %s, 'quarantined', %s)",
            (ref.id, batch_id, filename, period or "unknown", sha, reasons),
        )
        conn.execute("INSERT INTO quarantine_file (tenant_id, batch_id, content) VALUES (%s, %s, %s)", (ref.id, batch_id, content))
    return ImportResult("quarantined", period, batch_id, problems=problems, seconds=time.perf_counter() - started)

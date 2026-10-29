"""Moving one tenant's ledger lines between databases, rehearsed.

The order is the one the guide describes: copy, verify, switch the routing, and only after a full cycle delete the
old rows. Until the old rows are deleted, going back is one routing change; after, it is a copy in the other direction.

* ``copy_stream`` copies in id order with batched INSERTs (COPY FROM is refused under row-level security). It first
  clears any rows an earlier attempt left in the target, so a re-run does not duplicate.
* ``fingerprint`` is a row count, the two sums and a hash sum over the identifying columns, computed in each database
  as the tenant. The move is verified when the two fingerprints are identical.
* ``switch`` changes the tenant's routing (an operator action on the control table).
* ``retire`` deletes the old rows, and refuses unless the target still matches and the routing points at the target.

The rehearsal assumes the tenant's writes are paused for the copy, or that a second pass copies what arrived (call
``copy_stream`` again with ``after_id``); the switch is the freeze point.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID

from dclabaccount.config import ROOT
from dclabaccount.db import Router, TenantRef, admin_connection

BATCH = 5000
COLUMNS = "tenant_id, id, journal, txn_id, line_no, date, account_code, debit, credit, description, party_id, batch_id"
FINGERPRINT = """
SELECT count(*) AS lines, coalesce(sum(debit), 0) AS debit, coalesce(sum(credit), 0) AS credit,
       coalesce(sum(hashtextextended(id::text || '|' || txn_id || '|' || account_code || '|' || debit::text || '|' || credit::text, 0)::numeric), 0) AS hash,
       coalesce(max(id), 0) AS max_id
FROM ledger_line
"""


class MoveRefused(Exception):
    pass


@dataclass
class MoveReport:
    tenant: str
    source: str
    target: str
    lines: int = 0
    copy_seconds: float = 0.0
    verified: bool = False
    switched: bool = False
    retired_lines: int = 0
    steps: list[str] = field(default_factory=list)


def _database(key: str | None) -> str:
    return key or "shared"


def fingerprint(router: Router, ref: TenantRef, database: str) -> dict:
    with router.tenant_tx_for(ref, database) as conn:
        return dict(conn.execute(FINGERPRINT).fetchone())


def copy_stream(router: Router, ref: TenantRef, source: str, target: str, *, after_id: int = 0) -> int:
    """Copy the tenant's lines with id > ``after_id`` from ``source`` to ``target``. Returns how many were copied."""
    if source == target:
        raise MoveRefused("source and target are the same database")
    if after_id == 0:
        with router.tenant_tx_for(ref, target) as conn:
            conn.execute("DELETE FROM ledger_line")
    copied, last = 0, after_id
    while True:
        with router.tenant_tx_for(ref, source) as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM ledger_line WHERE id > %s ORDER BY id LIMIT %s", (last, BATCH)
            ).fetchall()
        if not rows:
            break
        with router.tenant_tx_for(ref, target) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO ledger_line ({COLUMNS}) OVERRIDING SYSTEM VALUE VALUES "
                    "(%(tenant_id)s, %(id)s, %(journal)s, %(txn_id)s, %(line_no)s, %(date)s, %(account_code)s, %(debit)s, %(credit)s, "
                    "%(description)s, %(party_id)s, %(batch_id)s)",
                    rows,
                )
        copied += len(rows)
        last = rows[-1]["id"]
    # the target's id sequence must continue after the copied ids (an operator action: the application role may not setval)
    with admin_connection(target) as admin:
        admin.execute("SELECT setval(pg_get_serial_sequence('ledger_line', 'id'), greatest((SELECT coalesce(max(id), 0) FROM ledger_line), nextval(pg_get_serial_sequence('ledger_line', 'id'))))")
    return copied


def verify(router: Router, ref: TenantRef, source: str, target: str) -> tuple[bool, dict]:
    a, b = fingerprint(router, ref, source), fingerprint(router, ref, target)
    return a == b, {"source": a, "target": b}


def switch(slug: str, key: str | None) -> None:
    with admin_connection("shared") as admin:
        admin.execute("UPDATE app.tenant SET database_key = %s WHERE slug = %s", (key, slug))


def retire(router: Router, user_id: UUID, slug: str, old: str) -> int:
    """Delete the old rows, after checking the move once more."""
    ref = router.resolve(slug, user_id)
    if ref.ledger_database == old:
        raise MoveRefused("the routing still points at the database whose rows would be deleted")
    ok, details = verify(router, ref, old, ref.ledger_database)
    if not ok:
        raise MoveRefused(f"the target no longer matches the source: {json.dumps(details, default=str)}")
    with router.tenant_tx_for(ref, old) as conn:
        return conn.execute("DELETE FROM ledger_line").rowcount


def move_stream(router: Router, user_id: UUID, slug: str, to_key: str | None) -> MoveReport:
    """Copy, verify and switch. The old rows stay until ``retire``."""
    ref = router.resolve(slug, user_id)
    source, target = ref.ledger_database, _database(to_key)
    report = MoveReport(slug, source, target)
    started = time.perf_counter()
    report.lines = copy_stream(router, ref, source, target)
    report.copy_seconds = round(time.perf_counter() - started, 2)
    report.steps.append(f"copied {report.lines} lines in {report.copy_seconds} s")
    report.verified, details = verify(router, ref, source, target)
    report.steps.append("fingerprints identical" if report.verified else f"fingerprints differ: {details}")
    if not report.verified:
        raise MoveRefused(f"not switching: the copy does not match ({details})")
    switch(slug, to_key)
    report.switched = True
    report.steps.append(f"routing switched to {target}")
    return report


def rehearse(slug: str = "bigco", out: Path = ROOT / "reports" / "tenant-move.json") -> dict:
    """The whole cycle on a seeded tenant: move out, read, roll back, move again, retire, move back. Writes a report."""
    from dclabaccount.queries import QUERIES

    router = Router()
    try:
        with admin_connection("shared") as admin:
            user = admin.execute(
                "SELECT m.user_id FROM app.membership m JOIN app.tenant t ON t.id = m.tenant_id WHERE t.slug = %s LIMIT 1", (slug,)
            ).fetchone()["user_id"]
        steps: list[dict] = []

        def balances() -> list[dict]:
            with router.tenant_tx(slug, user, store="ledger") as conn:
                return [dict(r) for r in QUERIES["account_balances"].run(conn)]

        def record(name: str, **data) -> None:
            steps.append({"step": name, **data})

        before = balances()
        first = move_stream(router, user, slug, "heavy")
        record("move to the dedicated database", **asdict(first))
        record("reads after the switch equal reads before", equal=balances() == before)
        switch(slug, None)
        record("rolled back before deleting anything: routing points at the shared database again", equal=balances() == before)
        second = move_stream(router, user, slug, "heavy")
        record("moved again (the target was cleared first, nothing duplicated)", **asdict(second))
        deleted = retire(router, user, slug, "shared")
        record("old rows deleted after the cycle", deleted=deleted, reads_equal=balances() == before)
        back = move_stream(router, user, slug, None)
        record("moved back to the shared database (a copy in the other direction)", **asdict(back))
        record("reads after the move back", equal=balances() == before)
        retire(router, user, slug, "heavy")
        result = {"tenant": slug, "steps": steps}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, default=str))
        return result
    finally:
        router.close()


if __name__ == "__main__":
    for item in rehearse()["steps"]:
        print(item)

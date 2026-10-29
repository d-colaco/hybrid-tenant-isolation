"""The reviewed cross-tenant exceptions.

A user who works for several tenants (an accountant with many clients) needs one result set across them. That is the
one place the tenant boundary is crossed on purpose, so it is a list kept like code (``exceptions.yaml``): each entry
names a statement, the hash of that statement as it was reviewed, the reviewer and the date. ``run`` refuses a
statement that is not listed or whose text changed since the review, and then asks the database for a read scope with
``app.enter_scope``, which refuses an exception that is not on its own copy of the list and records every use.
Nothing else in the code may call ``enter_scope`` (a test checks that).
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import yaml

from dclabaccount.config import ROOT
from dclabaccount.db import Router, admin_connection

LIST = ROOT / "exceptions.yaml"


class ExceptionNotReviewed(Exception):
    pass


@dataclass(frozen=True)
class ReviewedException:
    id: str
    description: str
    sql: str
    sql_sha256: str
    reviewer: str
    reviewed_on: str

    @property
    def actual_hash(self) -> str:
        return statement_hash(self.sql)


def statement_hash(sql: str) -> str:
    """The hash of a statement with its whitespace collapsed, so reformatting is not a change but any other edit is."""
    return hashlib.sha256(re.sub(r"\s+", " ", sql.strip()).encode()).hexdigest()


def load(path=LIST) -> dict[str, ReviewedException]:
    data = yaml.safe_load(path.read_text())
    return {
        item["id"]: ReviewedException(
            item["id"], " ".join(str(item["description"]).split()), item["sql"], str(item["sql_sha256"]), item["reviewer"],
            str(item["reviewed_on"]),
        )
        for item in data["exceptions"]
    }


def sync(path=LIST) -> int:
    """Copy the reviewed list into the database (an operator action). Entries whose hash does not match are refused."""
    entries = load(path)
    stale = [e.id for e in entries.values() if e.actual_hash != e.sql_sha256]
    if stale:
        raise ExceptionNotReviewed(f"statement changed since review: {', '.join(stale)}")
    with admin_connection("shared") as conn:
        conn.execute("DELETE FROM app.reviewed_exception WHERE id <> ALL(%s)", ([e for e in entries],))
        for entry in entries.values():
            conn.execute(
                """INSERT INTO app.reviewed_exception (id, description, sql_sha256, reviewer, reviewed_on)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET description = EXCLUDED.description, sql_sha256 = EXCLUDED.sql_sha256,
                       reviewer = EXCLUDED.reviewer, reviewed_on = EXCLUDED.reviewed_on""",
                (entry.id, entry.description, entry.sql_sha256, entry.reviewer, entry.reviewed_on),
            )
    return len(entries)


def run(router: Router, user_id: UUID, exception_id: str, *, entries: dict[str, ReviewedException] | None = None) -> list[dict[str, Any]]:
    entries = entries if entries is not None else load()
    entry = entries.get(exception_id)
    if entry is None:
        raise ExceptionNotReviewed(f"{exception_id} is not on the reviewed list")
    if entry.actual_hash != entry.sql_sha256:
        raise ExceptionNotReviewed(f"{exception_id}: the statement changed since it was reviewed")
    with router.pools["shared"].connection() as conn:
        conn.execute("SELECT app.enter_scope(%s, %s)", (user_id, exception_id))
        return conn.execute(entry.sql).fetchall()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "hash":
        print(statement_hash(load()[sys.argv[2]].sql))
    elif len(sys.argv) == 2 and sys.argv[1] == "sync":
        print(f"{sync()} reviewed exception(s) synced")
    else:
        sys.exit("usage: python -m dclabaccount.exceptions hash <id> | sync")

"""The tenant-scoped queries the application runs. Every one is registered, and none of them filters on the tenant:
isolation is the database's job (row-level security), not each query's. The registry is what the two-tenant test walks:
a new query is covered the moment it is registered, so a developer cannot forget to add it to the test.

Each query returns rows that carry ``tenant_id``, so a test can tell whose row it got.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import psycopg

QUERIES: dict[str, "TenantQuery"] = {}


@dataclass(frozen=True)
class TenantQuery:
    name: str
    store: str  # shared or ledger
    run: Callable[[psycopg.Connection], list[dict[str, Any]]]


def tenant_query(name: str, *, store: str = "shared"):
    def decorator(fn):
        if name in QUERIES:
            raise ValueError(f"query {name} registered twice")
        QUERIES[name] = TenantQuery(name, store, fn)
        return fn

    return decorator


@tenant_query("accounts")
def accounts(conn):
    return conn.execute("SELECT tenant_id, code, name FROM account ORDER BY code").fetchall()


@tenant_query("parties")
def parties(conn):
    return conn.execute("SELECT tenant_id, id, kind, name, email FROM party ORDER BY name").fetchall()


@tenant_query("deadlines")
def deadlines(conn):
    return conn.execute("SELECT tenant_id, kind, due_date, status FROM filing_deadline ORDER BY due_date").fetchall()


@tenant_query("import_batches")
def import_batches(conn):
    return conn.execute(
        "SELECT tenant_id, id, filename, period, status, line_count FROM import_batch ORDER BY created_at DESC"
    ).fetchall()


@tenant_query("account_balances", store="ledger")
def account_balances(conn):
    return conn.execute(
        """SELECT tenant_id, account_code, count(*) AS lines, sum(debit) AS debit, sum(credit) AS credit
           FROM ledger_line GROUP BY tenant_id, account_code ORDER BY account_code"""
    ).fetchall()


@tenant_query("recent_lines", store="ledger")
def recent_lines(conn):
    return conn.execute(
        "SELECT tenant_id, id, date, account_code, debit, credit FROM ledger_line ORDER BY date DESC, id DESC LIMIT 50"
    ).fetchall()


@tenant_query("dashboard", store="ledger")
def dashboard(conn):
    """What a quiet tenant's screen asks for: the last 30 days by account. The noisy-neighbour run times this."""
    return conn.execute(
        """SELECT tenant_id, account_code, count(*) AS lines, sum(debit) AS debit, sum(credit) AS credit
           FROM ledger_line
           WHERE date >= (SELECT max(date) FROM ledger_line) - 30
           GROUP BY tenant_id, account_code ORDER BY account_code"""
    ).fetchall()


@tenant_query("forgot_the_filter", store="ledger")
def forgot_the_filter(conn):
    """The mistake the two-tenant test exists for: no WHERE on the tenant. Row-level security answers for it."""
    return conn.execute("SELECT tenant_id, id, account_code, debit, credit FROM ledger_line ORDER BY id LIMIT 100").fetchall()

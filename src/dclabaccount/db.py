"""Connections that carry a tenant: the one way the application reaches tenant data.

A request names a tenant and a user. ``resolve`` asks the database (a SECURITY DEFINER function, because the
application role has no privilege on the control tables) whether the user belongs to the tenant and where its ledger
lines live. ``tenant_tx`` then opens a transaction and sets ``app.tenant_id`` **for that transaction only**
(``set_config(..., is_local => true)``): when the transaction ends the setting is gone, so the next request that
borrows the same pooled connection starts with no tenant and, under the policies, sees no rows.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from dclabaccount.config import Settings

DATABASES = ("shared", "heavy")


class NotFound(Exception):
    """The tenant does not exist, is not active, or the user does not belong to it: one answer for all three."""


@dataclass(frozen=True)
class TenantRef:
    id: UUID
    slug: str
    nif: str
    database_key: str | None
    status: str

    @property
    def ledger_database(self) -> str:
        return self.database_key or "shared"


class Router:
    def __init__(self, settings: Settings | None = None, *, pool_size: int = 8) -> None:
        self.settings = settings or Settings.load()
        self.pools: dict[str, ConnectionPool] = {
            name: ConnectionPool(
                self.settings.app_url(name), min_size=1, max_size=pool_size, kwargs={"row_factory": dict_row}, open=True,
                name=f"lab-{name}",
            )
            for name in DATABASES
        }

    def close(self) -> None:
        for pool in self.pools.values():
            pool.close()

    def resolve(self, slug: str, user_id: UUID) -> TenantRef:
        with self.pools["shared"].connection() as conn:
            row = conn.execute("SELECT * FROM app.resolve_tenant(%s, %s)", (slug, user_id)).fetchone()
        if row is None:
            raise NotFound(slug)
        return TenantRef(row["id"], slug, row["nif"], row["database_key"], row["status"])

    @contextmanager
    def tenant_tx(
        self, slug: str, user_id: UUID, *, store: str = "shared", _unsafe_session_scope: bool = False
    ) -> Iterator[psycopg.Connection]:
        """A transaction as the tenant. ``store`` is ``shared`` for the ordinary tables and ``ledger`` for the ledger
        lines, which live wherever the tenant's routing says. ``_unsafe_session_scope`` exists only so a test can show
        what a session-level setting would leak; nothing else may use it."""
        ref = self.resolve(slug, user_id)
        yield from self._tx(ref, "shared" if store == "shared" else ref.ledger_database, _unsafe_session_scope)

    @contextmanager
    def tenant_tx_for(self, ref: TenantRef, database: str) -> Iterator[psycopg.Connection]:
        """The same, for a caller that already resolved the tenant and names the database (the tenant move uses it)."""
        yield from self._tx(ref, database, False)

    def _tx(self, ref: TenantRef, database: str, unsafe: bool) -> Iterator[psycopg.Connection]:
        with self.pools[database].connection() as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, %s)", (str(ref.id), not unsafe))
            yield conn


def admin_connection(database: str = "shared", settings: Settings | None = None) -> psycopg.Connection:
    """The container superuser, for setup, seeding and operator actions. It bypasses row-level security by being a
    superuser; the application never uses it."""
    return psycopg.connect((settings or Settings.load()).admin_url(database), row_factory=dict_row, autocommit=True)

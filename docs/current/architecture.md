# Architecture

```text
 client (user + tenant name)
   │
   ▼
 API (FastAPI)  ──►  Router ── resolve_tenant (SECURITY DEFINER) ──► control tables (no privilege for the app role)
                       │
                       │ tenant_tx: BEGIN; set_config('app.tenant_id', …, local)
                       ├──────────────► PostgreSQL "shared": tenant tables, FORCE ROW LEVEL SECURITY
                       └──────────────► PostgreSQL "dedicated": the ledger lines of a heavy tenant, same policies
 operator (lab_admin): seed, route, move, purge ── bypasses row-level security, never used by the API
```

| File | Owns |
|---|---|
| `docker-compose.yml`, `db/init/01-roles.sh` | Two PostgreSQL 16 servers with the same limits (2 CPUs, 2 GB), and the application role |
| `migrations/` | Alembic: `0001` control schema and functions, `0002` tenant tables with policies, `0003` the slug lookup for the exception |
| `src/dclabaccount/db.py` | `Router`: resolve the tenant, pick the database, set the tenant per transaction |
| `src/dclabaccount/queries.py` | The registry of tenant queries, none of which filters on the tenant |
| `src/dclabaccount/exceptions.py`, `exceptions.yaml` | The reviewed cross-tenant exceptions, kept like code |
| `src/dclabaccount/saft.py`, `imports.py` | SAF-T generator, parser and validation rules; the import with quarantine and idempotent re-import |
| `src/dclabaccount/move.py` | The tenant move: copy, verify, switch, retire, rehearsal |
| `src/dclabaccount/offboard.py` | Export, erasure, retention and purge |
| `src/dclabaccount/bench.py` | The noisy-neighbour measurement |
| `src/dclabaccount/generator.py` | The fictional corpus |
| `src/dclabaccount/api.py` | A small HTTP surface |
| `scripts/` | `make_env.py`, `render_diagrams.py`, `screenshots.py` |
| `demo/` | The evidence package |

## Decisions and their reasons

- **Row-level security, forced, with a role that owns nothing.** The boundary sits in the database so a query that forgets the
  tenant filter returns nothing foreign. `FORCE` makes the policy apply even to a table's owner, and the application role owns no
  table and has no BYPASSRLS. A test reads the catalogue to assert both, and another that every public table is protected.
- **The tenant is set per transaction** (`set_config(..., is_local => true)`). With a connection pool, a session-level setting
  would carry the previous request's tenant to the next request. The negative control in the tests shows exactly that leak.
- **No privilege on the control tables.** The application role reaches tenants and memberships only through SECURITY DEFINER
  functions (`resolve_tenant`, `enter_scope`, `in_scope`, `tenant_slug`), so a bug in the application cannot list tenants.
  `resolve_tenant` returns no row for a missing, an inactive and a forbidden tenant alike: one answer for all three.
- **The cross-tenant exception is two checks and a record.** The code refuses a statement not on `exceptions.yaml` or whose
  whitespace-normalised hash changed since review; the database refuses an id not on its own copy of the list, grants a read
  scope tied to the current transaction id (not a setting the caller could set), and records each use. Only `exceptions.py` may call
  `enter_scope`; a test checks the source. **Limit:** the user id passed to `enter_scope` comes from the application, so this
  guards against application mistakes, not against someone who can run arbitrary SQL as the application role.
- **The heavy stream is the ledger lines only.** Accounts, parties, deadlines and batches stay in the shared database: they are
  small, and the cross-tenant exception reads them there. Imports write the two places in two transactions, so a failure between them
  leaves a batch marked `failed` with its lines removed and a re-import repairs it; the move is the same.
- **Move: copy, verify, switch, a full cycle, then delete.** Until the old rows are deleted, going back is one routing change.
- **Retention has no default.** The period is a legal figure to verify; the code requires the operator to set a date and refuses
  to purge without one.
- **No front end.** What matters here is shown by tests, reports and measurements; nothing in it is operated by a person.

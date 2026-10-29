# Features

## Isolation

- Every tenant table has `tenant_id` and a policy `tenant_id = app.current_tenant()` for reads and writes; the application role
  cannot read or write another tenant's rows, and an insert for another tenant is refused by the policy.
- `Router.tenant_tx(slug, user, store=…)` resolves the tenant (membership checked in the database), picks the database for the
  ledger lines and sets the tenant for the transaction only.
- The two-tenant test (`tests/test_isolation.py`) walks the query registry as two tenants and fails on any foreign row; a second
  test switches the policy off on one table and requires the same walk to find the leak.

## The reviewed cross-tenant exception

`exceptions.yaml` lists the statement, its reviewed hash, the reviewer and the date. `my_work_across_clients` returns an accountant's
open filing deadlines and quarantined imports across every client in one result set; a user who works for one client gets one
client's rows. The read policy exists only on deadlines and import batches and only opens inside a granted scope.

## SAF-T

`saft.generate` makes a synthetic monthly file; `saft.parse` reads untrusted XML with defusedxml (no entities, size cap);
`saft.validate` applies: watermark, taxpayer checksum and match, a one-month period, balanced transactions, declared totals,
known accounts, unique transaction ids, dates inside the period, sane lines. `imports.import_saft` quarantines a file that fails
any rule (reasons and bytes kept, nothing enters the ledger), does nothing for an identical file, and replaces the period's lines
for a corrected one.

## Move and offboarding

`move.move_stream` copies a tenant's ledger lines to the other database, verifies with a fingerprint (count, sums, a hash sum),
and switches the routing; `retire` deletes the old rows only after verifying again. `offboard` exports every table with a manifest,
erases personal data and quarantined raw files, keeps the accounting records, and purges them only after a retention date the
operator has set.

## Noisy neighbour

`make bench` seeds BigCo with 3 million ledger lines in the shared database, measures four quiet tenants' dashboard query in a closed
loop in three conditions (baseline, a heavy job on the shared database, the same job after BigCo's lines moved to the dedicated one),
repeats each, and writes `reports/noisy-neighbour.json`.

## HTTP

`GET /t/{slug}/{query}`, `POST /t/{slug}/imports`, `GET /me/work`; the caller names itself in `X-User`. A missing and a forbidden tenant
return the same 404.

## Commands

`make up`, `make sync-exceptions`, `make test`, `make bench`, `make move`, `make diagrams`, `make screenshots`, `make evidence`, `make down`, `make reset`.

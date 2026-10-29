# Isolating tenants: what the lab shows

## The problem

A B2B product serves many customers from one system. The boundary between them has to survive a developer who forgets a filter, a
connection pool that reuses connections, a user who works for several customers, and one customer whose data is so large it slows the rest.

## What the lab does

An accounting firm's back office on invented companies. Business data is shared and protected by PostgreSQL row-level security, forced,
with an application role that owns nothing and cannot bypass it. The tenant is set per transaction. The one cross-tenant read (an
accountant's work across clients) is an exception kept like code: a reviewed statement and hash, checked by the code and again by the
database, scoped to one transaction and recorded on every use. The heavy stream of one tenant, its ledger lines, can be moved to a
database of its own by a rehearsed procedure. SAF-T files are validated, quarantined when bad and safe to import twice. A tenant leaves
with an export, an erasure and a retention date.

## What it shows

- The two-tenant test: every query runs as two tenants and fails on any foreign row, and it is shown to fail when the policy is off.
- The pooled-connection test and its negative control: a session-level setting leaks the previous tenant to the next request.
- A noisy neighbour: quiet tenants' p95 went from 5.1 to 39.6 ms while a heavy tenant's job ran on the shared database, and back to
  4.5 ms once its ledger lines had a database of their own, with the same hardware limits and three repetitions.
- A rehearsed move of 3,000,000 lines, with a fingerprint check and a rollback until the old rows are deleted.
- SAF-T import: eight ways a file can be bad, all quarantined with their reasons; the same file twice changes nothing; a corrected file replaces the period.
- What building it found: COPY FROM is refused under row-level security, a 64 MB `/dev/shm` fails a heavy parallel query, a sequence
  is not copied with its rows, and a test that cannot fail proves nothing.

## Limits to state

- Not covered: authentication, billing and accounting features. The API names the caller in a header.
- The benchmark is one laptop, this data and this query. "Fixed" means back to the baseline, not faster than it.
- The SAF-T files are a subset of the PT structure and are not validated against the official XSD.
- The retention period of accounting records is a legal figure to verify with the accountant. The lab states none and the demonstration date is made up.
- The cross-tenant guard protects against application mistakes, not against someone who can run arbitrary SQL as the application role.

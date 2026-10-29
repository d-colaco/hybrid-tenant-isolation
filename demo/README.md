# Evidence package

A multi-tenant back office for an accounting firm, on synthetic data: tenants are isolated below the queries with PostgreSQL
row-level security, an accountant works for many tenants through a reviewed exception kept like code, the ledger lines of one heavy
tenant can be moved to a database of their own, SAF-T files are imported with quarantine and safe re-import, and a tenant is
offboarded with an export, an erasure and a retention date. This folder holds four diagrams, seven evidence frames, the figures and
the commands that produced them.

**Status:** a lab on invented companies, run on a laptop with Docker Compose. The SAF-T files are a subset of the
PT structure, not validated against the official XSD. The retention period of accounting records is a legal figure the lab does not
state: its tests use a made-up date.

## Run it

```bash
make install        # virtualenv, editable install, Playwright
make up             # two PostgreSQL servers (shared and dedicated), migrated
make sync-exceptions
make test           # 46 tests
make bench          # noisy neighbour: seeds 3 million ledger lines, three conditions, three repetitions
make diagrams       # demo/diagrams/*.svg and .png (needs Node and Chromium)
make screenshots    # demo/screenshots/*.png from live runs and from reports/noisy-neighbour.json
```

## Resources

| Resource | Where | What it is |
|---|---|---|
| Synthetic data | `src/dclabaccount/generator.py` | Companies named "Exemplo" with valid-checksum tax numbers, a small chart of accounts, parties at `example.test`, deadlines and ledger lines; every generated SAF-T file carries a watermark that the importer requires |
| Diagrams | `diagrams/` | Mermaid sources, light and dark SVG, and PNG |
| Evidence frames | `screenshots/` | Rendered from the lab's own output; listed below |
| Benchmark record | `../reports/noisy-neighbour.json` | Every run, with environment and command |
| Import throughput | `../reports/import-throughput.json` | One 100,000-line file |
| Presentation note | `presentation.md` | The problem, what the lab shows, the figures and the limits to state |

## Diagrams

| File | Caption (English) | Alt text |
|---|---|---|
| `diagrams/container.png` | The pieces: the router resolves the tenant in the database and sets it per transaction; two PostgreSQL servers, the second holding only a heavy tenant's ledger lines; the operator role never used by the API | Container diagram of client, API, router, a shared and a dedicated PostgreSQL |
| `diagrams/request-scope.png` | A tenant's request and the next request on the same pooled connection: the setting ends with the transaction, so the next request sees no rows | Sequence diagram of two requests sharing a connection |
| `diagrams/cross-tenant-exception.png` | The reviewed exception: refused unless listed, unchanged since review and on the database's own list; the scope lasts one transaction | Decision flow of a cross-tenant read |
| `diagrams/tenant-move.png` | Moving a tenant's lines: copy, verify, switch, a full cycle, then delete; rolling back is one routing change until the old rows are gone | Flow of a tenant move with its rollback paths |

## Screenshots

These are renderings of the lab's own output (the lab has no interface), in the order a walkthrough would show them.

| File | Caption (English) | Alt text |
|---|---|---|
| `screenshots/01-two-tenant-test.png` | Every registered query runs as two tenants and returns no foreign row; with the policy switched off the same walk finds 100, so a green run means something | Table of eight queries, rows and foreign rows for two tenants |
| `screenshots/02-reviewed-exception.png` | The reviewed exception: the list entry with its hash, what the same statement returns for an accountant of five clients, one of two and a user of one, what is refused, and the use recorded by the database | The exception entry and three result summaries |
| `screenshots/03-pooled-connection.png` | The tenant does not survive a pooled connection; the negative control shows what a session-level setting would leak | Three steps on one connection with rows seen |
| `screenshots/04-noisy-neighbour.png` | Quiet tenants' p95 per second: flat at baseline, about eight times higher while a heavy tenant's job runs on the shared database, flat again once its lines have their own database | Line chart of three conditions and a table of p50, p95 and p99 |
| `screenshots/05-tenant-move.png` | A tenant move rehearsed: move, read, roll back, move again, delete, move back; every read equals the read before | Table of seven steps with checks |
| `screenshots/06-saft-import.png` | SAF-T import: an identical file changes nothing, a corrected one replaces the period, and eight kinds of bad file are all quarantined with their reasons | Two tables: re-import and eight corruptions |
| `screenshots/07-offboarding.png` | Offboarding: a verified export first, personal data erased, accounting records kept, and a purge refused until a retention date is set and has passed | Export manifest, erasure report and purge outcomes |

## Figures

| Metric | Result | Measured on | Command |
|---|---|---|---|
| Quiet tenants' p95, baseline | 5.1 ms (5.09, 5.10, 5.13) | 2026-10-19, 3,000,000 lines for the heavy tenant, 8 quiet client threads | `make bench` |
| Quiet tenants' p95, heavy job on the shared database | 39.6 ms (51.61, 39.46, 39.59) | same | `make bench` |
| Quiet tenants' p95, heavy job on its own database | 4.5 ms (4.25, 4.49, 5.37) | same | `make bench` |
| Heavy job, median run | 5.2 s shared, 3.8 s on its own database | same | `make bench` |
| Move of 3,000,000 ledger lines | 93.9 s to copy, verified; 105.5 s with deletion | same | `make bench` |
| SAF-T import of 100,000 lines (51 MB) | 5.5 s in total, about 18,000 lines per second | 2026-10-19 | `python scripts/measure_import.py` |
| Tests | 46 passed | 2026-10-19 | `make test` |

## Rules for anything shown from here

- Synthetic data only: every company, tax number and address is invented; never a real SAF-T file or a client's data.
- The benchmark describes one laptop, this data and this query. Say that wherever it is quoted, and do not present "fixed" as faster than the baseline: the two are within the spread.
- State no retention period for accounting records. The demonstration date is made up.
- Retake the frames when a rule, the seed or a figure changes: `make screenshots` (and `make diagrams`).

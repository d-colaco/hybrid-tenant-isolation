# Verification

Run on 2026-10-19 (Python 3.11, PostgreSQL 16.11 in Docker, one laptop with 12 cores):

| Command | Result |
|---|---|
| `make test` | 46 passed in about 15 s (needs `make up`) |
| `make lint` | clean |
| `make bench` | `reports/noisy-neighbour.json`: see below |
| `python scripts/measure_import.py` | `reports/import-throughput.json`: 100,000 lines (a 51 MB file) generated in 2.8 s, parsed and validated in 2.1 s, imported in 5.5 s in total (about 18,000 lines per second, batched INSERTs) |
| `make move` | `dclabaccount.move.rehearse` on the small corpus: move, read, roll back, move again, delete, move back; every read equals the read before |

## Noisy neighbour

Four quiet tenants (8 client threads, a 20 ms think time) run their dashboard query; BigCo has 3,000,000 ledger lines and its heavy job
runs in 3 sessions. Both containers have 2 CPUs and 2 GB. 30 s per run after a 4 s warm-up, 3 repetitions per condition.

| Condition | p50 ms | p95 ms (median) | p95 of each repetition | p99 ms | Heavy run (s) |
|---|---|---|---|---|---|
| Baseline, no heavy job | 3.39 | **5.1** | 5.09, 5.10, 5.13 | 6.04 | - |
| Heavy job on the shared database | 3.31 | **39.6** | 51.61, 39.46, 39.59 | 55.11 | 5.2 |
| Baseline again, after BigCo's move | 3.06 | 4.7 | 4.88, 4.70, 4.30 | 5.67 | - |
| Heavy job on its own database | 2.62 | **4.5** | 4.25, 4.49, 5.37 | 6.49 | 3.8 |

What it shows and does not show:

- The heavy job moved the quiet tenants' p95 from 5.1 to 39.6 ms (about 8 times) and left the p50 where it was: the damage is in the tail.
  The quiet tenants completed about 7,000 requests in a window instead of about 10,200.
- With BigCo's lines in their own database the quiet tenants' p95 was 4.5 ms, back to the baseline. The difference between the
  baseline and "fixed" (4.7 against 4.5 ms) is inside the run-to-run spread; the fix is not a speed-up beyond the baseline.
- The fix did not starve the heavy job: it ran in a median 3.8 s on its own database against 5.2 s on the shared one (24 runs
  against 18 in a window), because it no longer competes with the others.
- It describes this laptop, this data, this query and containers with equal limits; the placement is the only difference.
- Moving BigCo's 3,000,000 lines took 93.9 s to copy (about 32,000 lines per second, batched INSERTs, verified by fingerprint) and
  105.5 s with the deletion of the old rows. Lines written after the copy would need a second pass; the rehearsal pauses the tenant.

An earlier full run died with "No space left on device" on a shared memory segment while the heavy job ran: Docker's 64 MB `/dev/shm`
is too small for parallel query workers. Both containers now have `shm_size: 1g` (see [findings.md](findings.md)).

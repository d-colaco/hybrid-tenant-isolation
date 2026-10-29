# Findings

What building it showed, beyond what the design said.

- **COPY FROM is refused on a table with row-level security** ("COPY FROM not supported with row-level security"; the hint is to
  use INSERT). The import and the tenant move therefore use batched INSERTs (psycopg's `executemany` in pipeline mode), which is
  slower than COPY; the throughput is in [verification.md](verification.md). COPY TO (reading) is allowed.
- **A two-tenant test needs a query that returns something.** The first version passed for tenants with no import batches, which
  proved nothing; the test now fails when a query returns no rows for either tenant.
- **A test that cannot fail proves nothing.** The suite includes a negative control for the row-level policy (switched off on one table)
  and one for the pooled-connection setting (session level), and both must fail in the way expected.
- **SAF-T namespaces:** ElementTree writes `ns0:` prefixes unless the namespace is registered as the default; files written that way
  are valid XML but not what a SAF-T file looks like, and string-level corruption tests missed them until the prefix was fixed.
- **Integer overflow at scale:** a seed expression `g * 7919` overflowed a 32-bit integer at 1.5 million transactions; the small test
  corpora never reached it. The seed casts to bigint.
- **A sequence is not copied with the rows.** After a move the target's id sequence must continue after the copied ids; the application
  role may not `setval`, so the move does it as an operator step.
- **The retention period is a decision, not a constant.** Erasure and retention pull in opposite directions; the lab erases personal data
  at once, keeps the accounting records, and makes the retention date an explicit operator input with no default.
- **Docker's 64 MB `/dev/shm` fails a heavy parallel query.** The first full benchmark died with "could not resize shared memory
  segment ... No space left on device" while the heavy job ran: parallel workers allocate dynamic shared memory in `/dev/shm`, and a
  container gets 64 MB by default. Both containers now have `shm_size: 1g`. A heavy tenant's job can fail, not just slow others,
  when the host's limits are too tight.

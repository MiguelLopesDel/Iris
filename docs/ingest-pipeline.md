# Ingest pipeline

This document distinguishes the current implementation from the planned
high-throughput pipeline. The goal is durable, account-isolated media ingest
with bounded work and measurable behavior; later phases must preserve those
properties rather than treating this design as a promise of throughput.

## Current implementation

### Per-account SQLite writer

`SQLiteWriteCoordinatorRegistry` owns at most one writer for each normalized
account database path. Each coordinator creates, uses, and closes its SQLite
connection on its own thread. It has a bounded pending queue, a bounded batch,
and a short configurable batching window. A batch uses `BEGIN IMMEDIATE`, a
savepoint per callback, and one `synchronous=FULL` commit. Successful futures
resolve only after that commit. A callback failure rolls back only its
savepoint; a transaction or commit failure rejects the affected batch.

Callbacks are deliberately limited to database work on the supplied
connection. They must not perform network or filesystem I/O, load models,
close the connection, or issue transaction-control statements. Callers prepare
schema and files before submission, and wait for completion before exposing a
durable state or cleaning up files. Accepted operations are bounded; overload
is reported as retryable backpressure instead of growing memory without limit.

The registry never sends one account's operation to another account's writer.
When its account-worker limit is reached, it retires only an idle coordinator;
otherwise it rejects admission. A queued operation can be cancelled before
execution and will be skipped. Once execution starts, cancellation cannot undo
the database operation.

### Sync path using the writer

- Reservation requests, including batch idempotency and source registration,
  run in one account transaction. Batch processing stays linear in the number
  of submitted items; the request limit bounds that number.
- Chunk offsets and recovery reconciliation are committed through the account
  writer. File writes remain outside the SQLite callback.
- Completion claims destination paths before moving files. File content and
  directory entries are made durable before the state advances to
  `pending_processing`.
- Processing leases, retries, recovery state, and the non-AI catalog batch use
  the same account writer. Catalog registration waits for its FULL commit
  before duplicate cleanup or completion callbacks.
- The AI adapter also uses the writer for the final upload-ready catalog and
  change-feed transaction. Model execution and FAISS index creation remain
  outside the writer and outside the request-response durability boundary.

The writer is process-local. SQLite remains the cross-process authority and
serializes any legacy or external connections. Moving every database mutation
in the optional indexer behind this writer, or measuring and replacing those
writes, is still an explicit follow-up; this phase does not claim that all
application SQLite writes have been centralized.

## Planned phases

The order below is a roadmap, not a statement that these features already
exist.

1. **Single account writer (in progress):** route reservation, completion,
   processing state, recovery, and catalog writes through the bounded writer;
   verify account isolation, overload behavior, commit ordering, and crash
   recovery.
2. **Durability service:** generalize upload-file durability into a shared
   service with bounded concurrent fsyncs and a policy supplied by the
   performance lab. Keep the existing durability behavior until measurements
   justify a change.
3. **Batch ingest endpoint:** accept a bounded manifest and streamed payloads,
   admit each item transactionally, hash while writing, and return a result per
   item. Preserve the resumable endpoint for older clients and large files.
4. **Performance lab:** exercise the new endpoint and sweep item count, bytes,
   in-flight packets, fsync concurrency, and durability/database windows on
   representative disks and networks.
5. **Android client:** use the batch endpoint for suitable files, with bounded
   packets, per-account durable queues, and per-item retry behavior.

Each phase needs deterministic failure/rollback coverage and before/after
measurements. The performance target (roughly 90–100 MB/s on the reference HD
with 500 KB photos and sub-second p95 confirmation) is a goal to validate, not
a current guarantee. Report items/s and MB/s separately, together with
confirmation latency and server resource samples.

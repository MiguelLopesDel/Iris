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

### File durability service

`FileDurabilityService` groups the file and directory fsyncs of concurrent
requests. One coordinator thread starts a group with the first waiting
request, adds requests arriving within `durability_window_s` until the group
holds `fsync_concurrency` files, then fsyncs those files in parallel and each
distinct directory once. Requests arriving meanwhile form the next group. The
queue is bounded; a full queue is reported as retryable backpressure. It
orders file data only: the account writer's FULL commit, issued after the
barrier, makes the matching database state durable.

### Batch ingest endpoint

`POST /api/sync/ingest` receives a bounded batch of reserved uploads whole,
back to back; `X-Iris-Ingest` lists them as `upload_id:size` in body order
(limits from `IngestPolicy`: `max_items`, `max_bytes`). `SyncIngestPipeline`
runs each stage once per batch:

1. **Admission** (one writer transaction): checks each reservation, settles
   content the library already has as `duplicate`, and records the final
   library path with state `receiving` before any byte is written.
2. **Write and hash**: bytes go straight to those paths, hashed as they
   arrive. Pieces of several small files share one thread-pool hand-over;
   `os.write` and SHA-256 release the GIL on large buffers.
3. **Durability**: one `FileDurabilityService` barrier for the batch's files
   and directories.
4. **Commit** (one writer transaction, FULL): stored files move to
   `pending_processing` through the existing finalization record; files whose
   hash does not match are removed and their uploads marked `failed`.
5. **Catalog**: the existing batch catalog path (`register_finalized`).

Each upload gets its own result, shaped like `complete-batch`. A batch cut
short acknowledges nothing: its files are removed and its uploads return to
`uploading`. Devices see `receiving` as `uploading` at offset 0, so they send
the file again. At startup, recovery checks every upload left `receiving`:
a complete file with the declared hash continues as `finalizing`; anything
else is removed and reset. The resumable `PUT` path is unchanged, for large
files and older clients.

#### Fewer disk barriers per batch

On a spinning disk each FULL commit costs a trip to the database's WAL and a
cache flush, whatever its size. A batch now pays about one:

- **Admission at reservation.** A batch-ingest client sends `"ingest": true`
  with its reservation. The reservation's own transaction (one FULL commit,
  needed anyway) also records each new upload's final path and state
  `receiving`. The ingest request then only reads to confirm that and writes
  no admission. Anything else (a reset upload, content found since, an older
  client) still takes the admission transaction.
- **A relaxed final commit.** The batch's final commit (stored and cataloged)
  does not wait for the disk: the `receiving` rows (final path, size, hash)
  and the synced files already let recovery rebuild it after a power loss.
  The account writer commits operations submitted with `durable=False` with
  `synchronous=NORMAL`, and a FULL barrier follows within
  `relaxed_barrier_s` (1 s), with the next durable batch, or when it stops.
- **Feed rewind.** A power loss can undo the feed's last changes, which
  recovery records again. `GET /api/sync/changes` with a cursor past the
  feed's end answers `next_cursor` = the end and `reset: true`; devices store
  `next_cursor` as is, so they rewind without an app change.

`IngestPolicy.from_env()` reads `IRIS_INGEST_<FIELD>` overrides, so the
performance lab can sweep the policy without code changes.

## Planned phases

The order below is a roadmap, not a statement that these features already
exist.

1. **Single account writer (in progress):** route reservation, completion,
   processing state, recovery, and catalog writes through the bounded writer;
   verify account isolation, overload behavior, commit ordering, and crash
   recovery.
2. **Durability service (done):** used by the batch endpoint. The resumable
   `PUT` keeps its own per-chunk fsync until measurements justify a change.
3. **Batch ingest endpoint (done):** see above.
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

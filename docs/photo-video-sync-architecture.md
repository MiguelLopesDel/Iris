# Photo and video synchronization architecture

Status: target architecture and staged implementation plan. This document is
not a claim that every target behavior is already implemented. The current
implementation snapshot is summarized below and the API contract remains in
[`android-sync-contract.md`](android-sync-contract.md).

## Product goal

Iris should feel like a native gallery first and a backup client second. Opening
the app shows the phone's local library immediately, while cloud items and
upload/download state are merged into that view. The server is the durable,
account-scoped copy; the phone remains useful and browsable while offline.
Originals are uploaded unchanged. Thumbnails, metadata extraction, indexing,
and optional AI enrichment are downstream work, not prerequisites for a
successful backup.

## Core model

Represent an item with three independent dimensions instead of one overloaded
status:

| Dimension | Example states | Meaning |
| --- | --- | --- |
| Origin | `device`, `cloud`, `both` | Where a browseable copy is known to exist |
| Transfer | `not_eligible`, `queued`, `uploading`, `retry_wait`, `uploaded`, `downloaded`, `error` | Work underway between this device and this account's server |
| Server processing | `accepted`, `processing`, `ready`, `processing_error` | Server-side catalog/derivative work after original bytes are durable |

An upload is not complete merely because it left the local queue or the server
received a request. It becomes `uploaded` only after the server confirms a
durable original and idempotent receipt. Processing failures must not cause a
second upload of the original. User deletion and source removal are distinct
operations; losing a phone source does not silently delete its server copy.

## Data flow

```text
MediaStore change
      |
      v
Local catalog (instant gallery) -----> eligibility / account-scoped durable queue
      |                                             |
      |                                     batch reservation (control plane)
      |                                             |
      |                                per-file resumable byte stream
      |                                             |
      |                                durable server original + receipt
      |                                             |
      +<------ incremental account change feed <----+----> processing queue
```

The app reads the local catalog without waiting for the server. Server state is
merged asynchronously. Cache local and remote thumbnails independently and
fetch originals only when opened, explicitly saved offline, or shared.

## Client discovery and queue

- MediaStore is the source of truth for Android-discoverable photos/videos;
  never crawl arbitrary paths. Use generation/version checkpoints for
  incremental discovery and reconcile a full scan when the platform signals
  that its change history is no longer usable.
- Persist each discovered source identity and upload job before network I/O.
  Include server identity, account identity, device identity, MediaStore volume
  and item ID, generation, media kind, capture time, byte size, and content hash.
  A URI may become unreadable after deletion or permission revocation; surface
  that as a local-source error, not as a server-side deletion.
- Queue state and credentials are scoped to `(server, account, device)`. Switching
  account/server pauses the old queue; it never replays it with new credentials.
  Keep the old account's jobs for when that identity is selected again.
- Automatic backup starts only when the account's effective preferences say so.
  Persist Wi-Fi/cellular, charging, battery, included albums/folders, and
  photo/video choices. Apply them before enqueue and again before transfer.
- Keep the gallery responsive: local catalog first, stable keys, paged queries,
  thumbnail-sized decode, and bounded prefetch. Network state must not block
  scrolling.

## Transfer protocol

Separate high-frequency metadata/control requests from media bytes:

1. Batch-reserve a bounded group of upload jobs in one authenticated request.
   Each item has a stable client idempotency key and an independent result;
   a partially rejected batch does not invalidate accepted siblings.
2. Transfer each original as a streaming, resumable per-file upload. Store the
   confirmed server offset locally only after the server acknowledges it. After
   timeout, query server status and continue from the confirmed offset; never
   guess or append to an ambiguous boundary.
3. Batch-finalize completed files to reduce round trips, but retain
   per-file completion results and retry semantics. Batch size is a control
   plane optimization; it must not turn into one giant in-memory media payload.
4. Make chunk size, transfer concurrency, and batch size independent knobs.
   Use a small streaming buffer and bounded in-flight byte budget. Start with
   conservative concurrency; adapt it based on measured throughput, latency,
   errors, device constraints, and server response. Increase only when evidence
   shows the link/server can benefit. Large media may use resumable sequential
   chunks; parallel multipart ranges are a later optimization requiring
   measurements and server support.
5. Handle auth expiry by refreshing the same device session, then retrying the
   same idempotent operation. A revoked device stops and requires login.

The current app already has durable jobs, batch initialization/finalization,
resumable offsets, and streaming bodies. Current code uses 32 MiB as the
server-advertised chunk ceiling and a 64 KiB Android streaming buffer; those are
not equivalent to allocating a 32 MiB buffer. Android currently caps active
uploads at 16. Server writes are now grouped into a bounded 1 MiB buffer and
performed off the async request loop; it fsyncs before acknowledging an offset.
Treat these as implementation facts to measure and tune, not permanent
architecture constants.

The opt-in Android diagnostic already separates whole-queue wall throughput
from active payload-PUT throughput and records phase timings. It now also counts
server-confirmed items, including deduplicated items that required no payload,
so it can report items/second beside MiB/second. These are rolling local
aggregates, not telemetry uploaded to Iris. They establish a measurement seam;
they do not yet implement adaptive concurrency, per-account fairness, or a
repeatable real-device benchmark protocol.

Initial server-side recovery work is now in place: finalization records its
destination before moving the file; startup reconciles `finalizing`,
`pending_processing`, and interrupted `processing` rows; and catalog work is
serialized per library with SQLite-backed leases and heartbeats across server
processes. Transient processing failures are retried with bounded backoff, then
become `failed_processing` after four attempts. Same-filesystem placement uses
rename, while a separate media mount uses verified destination-side staging
before the final rename. A user/operator-triggered retry for terminal failures
remains future work; those rows are not silently retried forever.

## Server acceptance and recovery

The server must define a durable acceptance boundary. For each upload:

1. Authenticate account and device; validate quota, declared size, hash syntax,
   media metadata limits, and upload ownership.
2. Stream bytes to an account-owned temporary file, flush/fsync them, and only
   then durably acknowledge the contiguous confirmed offset in SQLite.
3. On finalize, validate the full size/hash, then persist an idempotent
   finalization intent (upload ID, final destination, expected content identity)
   before moving the original.
4. Move/rename the file to its final account-owned location. Use atomic rename
   on one filesystem, or verified destination-side staging plus atomic rename
   across filesystems; fsync file and relevant directories where supported.
5. In a database transaction, register or deduplicate the original, attach
   device/source provenance, mark the upload accepted, and append the change
   event. If a duplicate exists, record the origin without keeping a second
   original.
6. On restart/retry, reconcile incomplete finalization intents against temp and
   final files. Completion responses and retries return the recorded result;
   never strand a moved file as an unexplained upload or ask the client to send
   it again.

The DB and filesystem cannot share one atomic transaction. Therefore every
cross-boundary step needs a durable intent and deterministic recovery rule.
Concurrency protection must eventually work across processes, not only via an
in-process lock. Keep recovery bounded, observable, and safe to rerun.

## Processing and change feed

- Original acceptance is independent of thumbnail, metadata, search-index,
  embedding, and face/person work. Enqueue each downstream task durably and
  independently; retry one derivative without retransferring the original.
- AI/model processing remains disabled unless explicitly configured. A weak
  self-hosted server must be able to receive, list, and serve originals without
  it. Resource-heavy jobs need concurrency limits and backpressure.
- Use an account-scoped monotonic change feed. Apply each page transactionally
  on the client and advance its cursor only after all changes are committed.
  A lost/expired cursor triggers a paged reconciliation, not a silent partial
  gallery. Keep stable item IDs and revisions so repeated changes are harmless.
- Record provenance such as source folder as metadata. Derive folder views
  server-side/account-side rather than creating a physical server directory for
  every phone path. Treat source folder names as untrusted display data, not
  server paths. A future policy can choose whether uploads from application or
  download folders are excluded or grouped; never silently upload arbitrary
  files outside MediaStore media collections.

## Performance and observability

Collect phase timings and outcomes without recording private content:

- discovery: MediaStore query duration, rows scanned, new/changed rows;
- queue: eligible, queued, deferred-by-constraint, duplicate, failed counts;
- transfer: bytes, throughput, request/first-byte/ack latency, retries,
  confirmed-offset recovery, active workers, per-file duration;
- server: reservation/finalization duration, hash time, durable-move time,
  catalog transaction time, quota rejections, recovery counts, processing queue
  depth/age;
- user experience: tap-to-local-content, tap-to-cloud-preview, open-original,
  gallery first-contentful-item, scroll frame jank, offline detection and
  reconnect recovery.

Use device/account-safe correlation IDs and aggregate metrics. Never log
credentials, raw media, full local URIs, or private absolute paths. Optimize the
largest measured contributors (especially serial round trips, server I/O,
thumbnail decoding, and unbounded work), not presumed bottlenecks.

## Implementation sequence

1. Pin down invariants and current-vs-target behavior in API and repository docs.
2. Make server finalization durable, retry-safe, and recoverable across restart;
   cover file/DB failure windows and concurrent duplicate requests.
3. Make client account-scoped queue transitions and server receipts explicit;
   prove account switching cannot submit another account's jobs.
4. Measure an upload batch on a real/emulated device and server, then replace
   fixed concurrency with a bounded, adaptive policy and clear backpressure.
5. Make MediaStore incremental scanning/reconciliation and gallery merging
   robust; verify no network wait blocks local content.
6. Add a durable server processing queue and independently retryable derivatives.
7. Add destructive-action/source-deletion policy and full restore/recovery drills.
8. Use collected measurements to tune chunks, batches, concurrency, caching,
   and thumbnail policy; retain low-end-device baselines.

Each step should state its invariant, migration behavior, failure windows,
metrics, and rollout/rollback conditions. Do not expand the work into model
generation or broad media-processing features without an explicit product
decision.

# Iris repository guide

Iris is a self-hosted, multi-account photo and video library. The repository
contains a Python/FastAPI server, a web client, an Android client, deployment
scripts, and tests. Keep account and device boundaries explicit in every API,
background job, and persisted queue.

## Project layout

- `server.py`, `routers/`: FastAPI application and authenticated API routes.
- `core/`: per-account catalog, storage, ingestion, synchronization, auth,
  backup, quota, and media-processing services.
- Shared implementation classes already present in `core/`:
  - `FileDigest` (`core/file_digest.py`) streams SHA-256 calculations and can
    return the digest together with file size. Reuse it for file hashing; keep
    caller-specific chunk sizes when they are intentional.
  - `FingerprintProbeMasks` (`core/fingerprint_index.py`) generates the exact
    XOR masks used to probe fingerprint slices. Do not add another mask
    generator in duplicate-detection or indexing code.
  - `UtcTimestamp` (`core/timestamps.py`) formats second-precision UTC
    timestamps with a trailing `Z` for catalog metadata. Other timestamp
    formats may have different persistence/ordering contracts; do not normalize
    them without checking those contracts.
  - `UploadReservationStore` (`core/upload_reservations.py`) centralizes
    account quota calculation, upload-row insertion, and device-source
    registration for single and batched sync reservations. Batch idempotency
    and per-item outcomes are owned by `SyncUploadService`
    (`core/sync_upload_service.py`); HTTP status translation remains in the
    router.
  - `SyncUploadService` owns the account-scoped resumable-upload workflow;
    `routers/sync.py` is its authenticated HTTP adapter.
  - `UploadProcessingWorkers` (`core/upload_processing_workers.py`) is the one
    bounded in-process queue for post-acceptance processing, save-to-library
    jobs, and recovery submissions. Its queue is volatile; `sync_uploads` and
    processing leases in each account database are the durable source of
    truth, so unstarted work must remain eligible for recovery after shutdown.
- Android sync batching is shared through `CoalescingBatcher`
  (`android/app/src/main/java/com/iris/app/data/sync/CoalescingBatcher.kt`).
  Keep endpoint-specific request/response mapping and per-item semantics in
  `UploadInitBatcher` and `UploadCompleteBatcher`; do not duplicate the queue,
  coalescing-window, or max-batch loop.
- Responsibility review (audit on 2026-09-29; implementation notes are current
  through commits `096ca65`, `bbc493c`, and `3ebe944`):
  1. Android sync boundaries: `MediaPayloadSource.kt` owns provider-backed
     media hashing and bounded chunk streaming; `ResumableUploadTransfer.kt`
     owns the active per-item resumable protocol and its account-scoped durable
     state transitions. `SyncUploadManager.kt` owns queue claiming, worker
     concurrency, progress aggregation and batcher lifecycle. `IrisApplication.kt`
     owns account-change sync policy, and `IrisRepository.kt` exposes a broad
     consumer surface. Further candidates are account lifecycle coordination
     and narrow consumer interfaces, preserving account scoping.
  2. Android gallery composition: `GalleryViewModel.kt` combines MediaStore,
     local catalog, remote paging, session identity and origin merging. Review
     a small gallery data-source/repository seam and pure merge tests.
  3. Backend HTTP/upload boundary: extracted to `SyncUploadService`; the router
     retains authentication, request handling and HTTP error translation.
  4. Backend processing/recovery: the scanner now submits work to the same
     bounded `UploadProcessingWorkers` queue used by live uploads and
     save-to-library. Keep recovery state database-backed and preserve the
     separation between durable acceptance, catalog registration and optional
     AI/index work. Regression tests cover queue saturation/retry, duplicate
     submissions, and recovery after the volatile worker queue is drained at
     shutdown; shutdown timeout is logged for operations.
  5. Server composition and route ownership: `server.py` still combines
     application wiring, mutable global state, middleware and several domain
     routes. Continue domain-by-domain using existing routers; do not rewrite
     wholesale.
  6. Web composition and transport: `static/app.js` and `static/system.js`
     combine independent workflows, while some domain code bypasses
     `static/api.js`. Review incremental workflow extraction and consistent API
     error/session handling. `static/spaces.js` is relatively cohesive and
     should not be split only because it is large.
  These are analysis notes, not a mandate to refactor immediately. Prefer
  composition and narrow interfaces over inheritance unless real substitutable
  implementations emerge. Validate current behavior and tests before moving
  code; preserve unrelated local changes.
- `static/`, `templates/`: web client assets and HTML.
- `android/`: Kotlin/Jetpack Compose Android application; `android/app/src/main`
  is production code, with JVM and instrumentation tests under `src/test` and
  `src/androidTest`.
- `scripts/`: server lifecycle/backup/release tools and development utilities.
- `tests/`: Python API, persistence, migration, and release-contract tests.
- `docs/`: product, deployment, architecture, recovery, performance, and test
  contracts. Keep implementation state distinct from proposed target behavior.
- `data/`, `media/`, local databases, build output, and credentials are runtime
  or private artifacts and must not be committed.

## Common workflows

- Server (Docker Compose): `./scripts/server.sh install`, `status`, `logs`,
  `backup`, and `update`. Use `./scripts/server.sh port <1024-65535>` to change
  the published local port. Do not run lifecycle commands against a user's
  remote server unless explicitly asked.
- Python checks: `pytest`; lint with `ruff check core routers scripts tests`;
  compile with `python3 -m compileall -q core routers scripts tests`.
- Android: from `android/`, use `./gradlew :app:assembleDebug`,
  `./gradlew :app:testDebugUnitTest`, and `./gradlew :app:connectedDebugAndroidTest`
  when an emulator/device is available. `./scripts/test.sh` wraps common local
  build/device workflows.
- Server release checks: `./scripts/test_release.sh` (review its scope and
  environment requirements before invoking).

## Design and safety invariants

- Every library read/write is scoped to the authenticated account; device sync
  additionally requires a valid device session. Never trust account IDs,
  device IDs, or filesystem paths supplied by a client as authority.
- Android discovery uses MediaStore and stores durable work locally. Queue and
  credentials are scoped to the server and account; account switching must not
  submit one account's queue with another account's credentials.
- Preserve originals. Upload acceptance, catalog registration, thumbnails,
  optional AI/index processing, and client change-feed application are separate
  states. Do not report an item backed up until the server has durably accepted
  it. Retrying an operation must be idempotent or safely resumable.
- Do not run expensive AI/model work synchronously on the upload path. The
  default self-hosted server must be able to receive and browse uploads without
  embeddings or a GPU.
- Never log passwords, access/refresh tokens, media bytes, or private absolute
  paths. Keep `.env`, private media, local databases, and personal server config
  out of commits.
- Destructive library actions must use the product's recoverable trash/delete
  semantics; do not add direct filesystem deletion to UI flows.

## Code conventions

- Python uses type hints, focused functions, and `snake_case`; Kotlin follows
  existing package and Compose conventions. Prefer small, testable modules and
  explicit interfaces at persistence/network boundaries.
- Before introducing a helper or algorithm, check the shared classes above and
  reuse them when their behavior and persistence format match. Do not merge
  superficially similar logic whose compatibility or ownership contracts differ.
- Add deterministic regression coverage for behavior changes when permitted by
  the task. Avoid broad refactors and preserve unrelated work in a dirty tree.
- Use Conventional Commit messages, e.g. `fix(sync): make upload recovery durable`.

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

### Change, integration, and release workflow

- Keep `main` as the only permanent development branch. Start each task from an
  up-to-date `main` on a short-lived branch prefixed with the matching
  Conventional Commit type: `feat/`, `fix/`, `docs/`, `chore/`, `build/`,
  `ci/`, or `refactor/`; split work that cannot be reviewed and validated as one
  coherent change. Do not develop directly on `main` or keep completed branches.
- Use a pull request as the normal integration path, especially for outside
  contributions. Review the final diff, behavior, tests, migrations, and
  deployment impact. A solo PR can be self-reviewed; do not invent a second
  reviewer requirement, but do require the applicable CI checks. Merge only
  after checks pass, then delete the task branch. Never force-push shared
  branches or rewrite `main` history.
- Commits use English Conventional Commit messages and no attribution trailers.
  Keep each commit internally coherent and independently understandable;
  separate unrelated behavior, cleanup, dependency, and deployment changes.
  Prefer small batches that can be tested and integrated promptly over large,
  long-lived feature branches.
- Run targeted checks while developing and the applicable full checks before
  integration. CI covers Python, Docker release smoke, and Android JVM
  tests/builds; it compiles but does not yet execute Android instrumentation on
  a device. Android changes must also use the isolated instrumentation workflow
  for device-facing behavior; report clearly when sandbox/device limitations
  prevent a check. A successful build is not a substitute for a behavioral
  test.
- Treat merge and deployment as separate events. A commit on `main` is
  integrated, not automatically deployed. Before deployment, identify the
  exact commit, ensure the server worktree is clean, make/verify a backup, then
  update and check health, logs, and the running commit. Record the outcome and
  have a recovery path for both application code and persistent data.
- Releases: tagging `vX.Y.Z` (it must match `version` in `pyproject.toml`) runs
  `.github/workflows/release.yml`, which runs `scripts/test_release.sh` and
  publishes `ghcr.io/miguellopesdel/iris` as `X.Y.Z`, `X.Y`, `latest` and the
  matching `-cuda` tags. The running code is that image, selected by
  `IRIS_VERSION` in the server's `.env`, not the checked-out source.
- `scripts/server.sh update` backs up, runs `git pull --ff-only` in a git
  checkout (only for scripts and compose files), then pulls the image for
  `IRIS_VERSION`, building it from the checkout only when that tag was never
  published. Only run it on a server checked out to `main`, and prefer a pinned
  `IRIS_VERSION` over `latest` for deliberate upgrades. Backups protect data;
  rolling back code means restoring the previous `IRIS_VERSION`.
- Do not claim deploy-by-image-digest exists yet; tags are mutable. Database/
  schema changes must include migration, backup, compatibility, and rollback
  considerations.

- Server (Docker Compose): `./scripts/server.sh install` (detects an NVIDIA GPU
  and picks the image; `--gpu`/`--cpu` override), `setup-code`, `status`, `logs`,
  `backup`, and `update`. The first administrator is created in the browser at
  `/setup` with the one-time installation code (`core/first_setup.py`);
  `create-admin` is the terminal alternative. The compute device is chosen only
  in `core/compute_device.py`, which honours the administrator's `gpu` setting.
  Use `./scripts/server.sh port <1024-65535>` to change the published local port. Do not run lifecycle commands against a user's
  remote server unless explicitly asked.
- Python checks: `python scripts/check_deps.py`; `pytest`; lint with
  `ruff check core routers scripts tests server.py`; compile with
  `python3 -m compileall -q core routers scripts tests server.py`.
- Dependencies (details in `docs/dependency-profiles.md`): Python 3.13 on Linux
  x86_64. Declare direct dependencies only in `requirements*.in`, then run
  `scripts/lock_deps.sh` to regenerate the hashed `requirements*.txt` locks;
  never edit the locks by hand, and commit `.in` and locks together (CI fails
  when they diverge). Install locks only with
  `pip install --no-deps --require-hashes -r <lock>` into a dedicated venv, and
  verify with `scripts/check_deps.py` (`--cuda` for the NVIDIA profile), not
  `pip check`. Never let `opencv-python` or, in the CUDA profile, CPU
  `onnxruntime` into an environment: insightface declares them and they shadow
  `opencv-python-headless` / `onnxruntime-gpu`. Dependency upgrades need a real
  model run (indexing with EasyOCR, Florence-2, CLIP, Whisper and faces), not
  only the fake-model test suite.
- Docker: one `Dockerfile` builds both profiles (`--build-arg
  IRIS_PROFILE=cpu|cuda`); `docker-compose.gpu.yml` only overrides the image
  and GPU access and is enabled with `COMPOSE_FILE` in `.env`. Keep runtime
  settings in `docker-compose.yml` so both profiles share them.
- Android: from `android/`, use `./scripts/test.sh fast`,
  `./scripts/test.sh build`, `./scripts/test.sh device-smoke`, and
  `./scripts/test.sh compose-smoke` / `./scripts/test.sh maestro-smoke`. The script
  scopes instrumentation to an emulator and the isolated lab application.
- Mobile test loop: run JVM/Compose unit tests first; use the `lab` app variant
  and `./scripts/test.sh device-smoke` for repeatable UI smoke checks. The lab
  variant uses the separate `com.iris.app.lab` package so its URL, credentials,
  preferences, and local database cannot overwrite the installed Iris app.
  Device/instrumentation scripts must select an `emulator-*` serial explicitly
  and refuse physical phones. Use physical devices only for deliberate manual
  acceptance and performance checks; never run test suites that mutate app
  state or write test media into a personal installation/library. Keep test
  accounts, servers, and media disposable and isolated.
- For agent-assisted UI exploration, prefer stable Compose semantics and
  accessibility labels over screen coordinates. Convert every confirmed bug
  into a deterministic unit/UI flow before relying on further agent exploration.
  Use Maestro flows/MCP for end-to-end journeys; setup and the project-scoped
  Codex MCP entry (`maestro-smoke`) live in `docs/android-testing-workflow.md` and
  `.codex/config.toml`. Keep flows checked in and reproducible, not ad hoc
  AI-only sessions. Use physical-device Macrobenchmark only for measured performance
  questions, and Firebase Test Lab/device matrices for broader compatibility,
  not as the inner development loop.
- ADB: use approved `adb` commands directly without asking again for each
  command. If the sandbox specifically blocks ADB's local daemon socket, group
  the required read/test actions into one escalated invocation rather than
  retrying approval per command. Never target a physical serial from automated
  test scripts.
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

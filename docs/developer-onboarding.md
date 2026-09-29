# Developer onboarding map

This is a map of the current code, not a target architecture. When behavior is
unclear, follow the route and its tests; design documents may describe planned
behavior rather than a finished implementation.

## Read in this order

1. [Product vision](product-vision.md) for accounts, private libraries and
   shared spaces.
2. [Implementation roadmap](implementation-roadmap.md) for current gaps and
   work not yet validated.
3. [Testing guide](testing.md) and [contribution setup](../CONTRIBUTING.md) to
   run the smallest relevant checks.
4. The domain contract linked below before changing sync, recovery or storage.

## Application and request path

```text
uvicorn server:app
  -> server.py builds the FastAPI app and shared runtime state
  -> lifespan starts/stops backends, workers and recovery tasks
  -> middleware handles session/authentication and binds a private account
  -> a router or a route still in server.py handles the HTTP request
  -> core/ owns domain and persistence operations
  -> response returns to the web or Android client
```

`server.py` is still the composition root, but it also owns many legacy-library
routes and helpers. A function being in `server.py` does not automatically mean
it is application wiring; trace its callers and tests before moving it.

### Two server modes matter

- **Legacy single-library mode:** existing `/api/...` gallery, search, import,
  and catalog-backup routes in `server.py` use the process-wide backend and
  configuration. The catalog backup endpoints are `/api/backup/*`.
- **Private multi-account mode:** authentication middleware binds requests to a
  user and that user's backend. Auth, device sync, shared spaces, and admin
  routes live in `routers/auth.py`, `routers/sync.py`, `routers/spaces.py`, and
  `routers/admin.py`. Host-global legacy settings/filesystem/catalog-backup
  routes are deliberately unavailable here. `/api/admin/backups` is the
  separate whole-instance backup interface for this mode.

Do not assume that an endpoint available in one mode is available in the
other. Keep account identity explicit in routes, services, database access and
background work.

## Follow the main product flows

### Browser gallery

`static/app.js` composes the web application; `static/api.js` is the shared API
transport. Gallery and search modules call the corresponding HTTP routes. The
paginated gallery and timeline routes (`/api/records` and
`/api/records/timeline`) are in `routers/records.py`; their existing catalog,
sorting, filtering and serialization operations are wired from `server.py`.
Record detail/edit and search endpoints remain in `server.py`. Start at the UI
event, find the API call, then follow that exact route rather than searching
only by a similarly named core function.

### Android gallery and server connection

`IrisNavGraph` selects a screen; the screen observes a `ViewModel`; the
ViewModel uses `IrisRepository` and the data-source interfaces; those use
`IrisApiService`/`IrisApiClient` and local catalog/device-media stores. Gallery
composition is concentrated in `GalleryViewModel`, so check its tests and
`GalleryRecordMerger` when changing local/remote merging or origin/status
display.

### Android upload to durable server acceptance

```text
MediaStore
  -> MediaStoreScanner / local durable queue
  -> SyncUploadManager
  -> UploadInitBatcher + ResumableUploadTransfer + UploadCompleteBatcher
  -> routers/sync.py (authenticated HTTP adapter)
  -> SyncUploadService + UploadReservationStore
  -> account database and durable original
  -> UploadProcessingWorkers for downstream work
```

The server's durable acceptance of the original is distinct from thumbnail,
metadata, search-index or AI processing. Do not make upload success depend on
optional AI work. Account/device scoping and crash recovery are part of the
contract, not implementation details. See [Android sync contract](android-sync-contract.md)
and [sync architecture](photo-video-sync-architecture.md).

### Backups: distinguish the two systems

- `/api/backup/*` snapshots a legacy catalog and provides legacy media reconcile
  and export operations. Its HTTP adapter is `routers/backup.py`; catalog and
  file operations remain in `core/backup.py`, while active legacy backend/path
  callbacks are wired from `server.py`.
- `/api/admin/backups` is for multi-account whole-instance backups. Its route is
  in `routers/admin.py`; scheduling and durable backup-run behavior live in
  `core/backup_scheduler.py` and snapshot operations in `core/instance_backup.py`.

These are intentionally different scopes and must not be combined merely
because both are called “backup.”

## Where to validate a change

- HTTP routes and legacy behavior: `tests/test_api.py` plus focused route tests.
- Domain persistence/recovery: the matching `tests/test_<domain>.py` and the
  core module it exercises.
- Android sync: `android-sync-contract.md`, JVM tests, then instrumentation
  tests when a device/emulator is available.
- Release/deployment behavior: `docs/server-deployment.md`,
  `docs/pilot-readiness.md`, and `scripts/test_release.sh`.

Prefer one flow-specific test command first, then the broader suite appropriate
to the change. Avoid running model-heavy indexing or deployment lifecycle
commands just to validate a route extraction.

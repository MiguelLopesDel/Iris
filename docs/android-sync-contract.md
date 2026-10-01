# Android synchronization contract

The Android app is an Iris device client. It must not call the manual browser import
endpoint (`/api/import`) and must not access server filesystem paths.

The web interface uses the same protocol: a signed-in browser is a device
(`platform=web`) authenticated by its session cookie instead of bearer tokens.
It appears in `GET /api/sync/devices` and, once revoked, its session ends on the
next request.

## Authentication and device identity

1. `POST /api/auth/devices/login` as form data: `username`, `password`,
   `device_name`, `platform=android`.
2. Store `access_token`, `refresh_token`, and `device_id` only in Android Keystore
   backed storage. Send `Authorization: Bearer <access_token>` for every sync call.
3. Access tokens expire after 900 seconds. Renew with `POST /api/auth/devices/refresh`
   form data: `device_id`, `refresh_token`. Replace both tokens atomically.
4. A revoked device receives `401`; erase its local credentials and stop background work.

## Upload protocol

For every locally captured media item, persist an upload job in the app database before
making a network request. The job stores local URI, filename, byte size, SHA-256,
capture timestamp, `upload_id`, and next byte offset.

1. `POST /api/sync/uploads` JSON: `filename`, `size`, `sha256`, `captured_at`, and
   an optional `source` object containing the opaque MediaStore source ID, display
   name, relative path, volume, item ID, generation, and media kind. The server stores
   these as metadata and must never resolve a client relative path on its filesystem.
2. The response supplies `upload_id`, `offset`, and `chunk_size` (currently 32 MiB).
   When the library already holds content with that SHA-256, the reservation is
   settled immediately with `state: "duplicate"` and the existing `media_id`: send
   no bytes and do not call `complete`. Such a reservation does not count against
   the quota, and the device/source is recorded as an origin of that media.
3. `PUT /api/sync/uploads/{upload_id}?offset={offset}` sends raw bytes, not multipart.
   Send chunks sequentially, then store the returned offset transactionally. The
   server fsyncs acknowledged bytes before advancing the confirmed offset.
4. Retry the same offset after an interrupted request. On `409`, obtain the confirmed
   position with `GET /api/sync/uploads/{upload_id}` before retrying; never guess it.
5. `POST /api/sync/uploads/{upload_id}/complete` verifies the full SHA-256 and
   begins finalizing the original into the account-owned server library. The
   server persists its intended destination before moving the file, so a retry
   can recover if the server stops between the filesystem move and catalog
   registration.

### Batch requests and partial success

- `POST /api/sync/uploads/batch` reserves 1–16 jobs at once. Each item has a
  stable `client_upload_id`; retrying it with identical metadata returns the
  existing reservation. An item-level `error_code` does not invalidate sibling
  reservations.
- `POST /api/sync/uploads/complete-batch` finalizes 1–16 upload IDs. Results are
  per item; retry only failed/transient items. Byte transfer is still
  per-media, streamed, resumable, and independently acknowledged.
- Batch endpoints reduce control-plane round trips only. They do not combine
  media bytes into one request or imply atomic all-or-nothing behavior.

The completion state `pending_processing` means the original has been accepted and
durably placed in the account library, but catalog/derivative work is not complete.
The change feed advances it through `processing` to `ready`, or to
`failed_processing` with a safe error summary. Do not re-upload an item only because it
is processing or processing failed. `duplicate` means the original was already present
in the same private library; its device/source provenance is still recorded.

In multi-user mode, server startup scans each private library for durable
`finalizing`, `pending_processing`, and interrupted `processing` uploads. It
reconciles the persisted file destination, then resumes catalog work one item at
a time. The processing mode follows server configuration; when sync AI processing
is disabled, this recovery only registers media and does not generate embeddings
or run face/model work. Processing is protected by database-backed per-library
leases across server processes. Transient failures retry automatically with
bounded backoff (up to four attempts); terminal `failed_processing` items still
need an operator/user retry flow, which is not yet exposed.

Finalization is an idempotent operation keyed by server upload ID. The server records a
`finalizing` intent and destination before its file move. If a completion response is
lost or the server restarts at that boundary, retry `/complete`; the server validates
the final file (or remaining temporary file), completes the durable move, and resumes
catalog registration. If downstream registration is asynchronous, the response stays
`pending_processing` until the change feed reports its terminal state. An unrecoverable
missing/corrupt file is a server recovery error, not permission to silently mark the
client item backed up.

## Reading changes

`GET /api/sync/changes?cursor={cursor}&limit=200` returns an ordered account-scoped change
feed. Persist `next_cursor` only after applying all returned changes locally. Poll on app
open, after upload completion, and through scheduled background work; no push service is
required for the first release.

## Android behavior

- Discover new media with MediaStore, never arbitrary filesystem crawling.
- Automatic synchronization defaults to off. Let the user include all MediaStore
  sources or an explicit set, and select photos, videos, or both.
- Treat source folders as derived views, not user albums. Deselecting a source stops
  new uploads and never deletes an accepted original.
- Use WorkManager with a durable local database queue. Default constraints: network
  required; expose Wi-Fi-only and battery preferences to the user.
- A run that uploads media becomes a foreground `dataSync` service with an ongoing,
  silent notification (rate, items sent, bytes left), so Android does not stop it at
  its few-minute limit for background work. Android 12+ only lets a run started with
  the app closed become that service when the user exempted the app from battery
  optimization; the sync screen asks for that exemption and for notification
  permission while automatic backup is on. When Android refuses, the run continues as
  ordinary background work and the run history records the refusal.
- Never cancel a sync that is uploading to start another: app start, login and
  setting changes only replace a one-time sync that is still waiting for its
  constraints. A second runner that finds the queue busy has nothing to do and is not
  a failure.
- An item whose local media was deleted before it was uploaded is failed with
  "O arquivo não existe mais no aparelho" and the queue moves on; retrying it would
  stop the whole backup on every run. Only a read failure with the file absent counts
  as deletion, never a network or server error.
- Keep a local history of runs (trigger, whether the app was open, bytes, items,
  foreground-service outcome, and the WorkManager stop reason) so a user can verify
  afterwards that backup progressed with the app closed.
- Display server thumbnails/listing as cache. Do not download originals unless opened,
  explicitly saved offline, or shared.
- Keep the app usable while uploads run; show pending, uploading, backed up/processing,
  ready, failed, and duplicate states.
- Never put passwords, refresh tokens, media bytes, or absolute local paths in logs.
- Treat a `401` as a revoked/expired session and require a login after refresh fails.

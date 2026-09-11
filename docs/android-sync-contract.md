# Android synchronization contract

The Android app is an Iris device client. It must not call the manual browser import
endpoint (`/api/import`) and must not access server filesystem paths.

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
3. `PUT /api/sync/uploads/{upload_id}?offset={offset}` sends raw bytes, not multipart.
   Send chunks sequentially, then store the returned offset transactionally.
4. Retry the same offset after an interrupted request. On `409`, obtain the confirmed
   position with `GET /api/sync/uploads/{upload_id}` before retrying; never guess it.
5. `POST /api/sync/uploads/{upload_id}/complete` verifies the full SHA-256 and moves
   the original into the account-owned server library.

The completion state `pending_processing` means backup succeeded but metadata, thumbnail,
and AI indexing are not ready yet. The change feed advances it through `processing` to
`ready`, or to `failed_processing` with a safe error summary. Do not re-upload an item
only because it is processing. `duplicate` means the original was already present in the
same private library.

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
- Display server thumbnails/listing as cache. Do not download originals unless opened,
  explicitly saved offline, or shared.
- Keep the app usable while uploads run; show pending, uploading, backed up/processing,
  ready, failed, and duplicate states.
- Never put passwords, refresh tokens, media bytes, or absolute local paths in logs.
- Treat a `401` as a revoked/expired session and require a login after refresh fails.

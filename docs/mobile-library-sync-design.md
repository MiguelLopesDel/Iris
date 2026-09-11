# Mobile library synchronization design

## Scope and product rules

The Iris server remains the source of truth for each private library. Android presents
one gallery assembled from the phone's `MediaStore`, the server catalog mirror, and the
durable upload queue.

The first release must:

- let the user explicitly enable automatic synchronization;
- synchronize photos, videos, or both;
- include every visible device folder or only selected folders;
- preserve the device and source-folder identity of every upload;
- show `device only`, `queued`, `uploading`, `processing`, `available in Iris`, and
  `Iris only` states without duplicating the same item in the grid;
- let a user pause a noisy source and later select all its server items for cleanup;
- survive process death, network loss, token refresh, duplicate retries, and server
  restarts without uploading the same bytes twice;
- never delete an original merely because a folder was deselected.

Deletion is deliberately not an implicit two-way mirror. The UI must offer separate,
explicit operations: **remove from this device**, **remove from Iris**, and **remove
everywhere**. The last operation needs an extra confirmation and server tombstones.

Non-goals for the first release are arbitrary filesystem crawling, instant push,
end-to-end encryption, multi-server replication, and downloading all originals to the
phone.

## Scale and service targets

Planning case: 20 household users, 5 devices per user, 25,000 items per user, average
file size 5 MiB. That is roughly 500,000 items and 2.5 TiB of originals. A burst of 20
phones uploading at 10 Mbit/s each needs about 200 Mbit/s of inbound bandwidth and
25 MiB/s of sustained disk writes. Metadata traffic is small (normally below 10 QPS),
while a cold gallery can create hundreds of thumbnail requests.

Targets on a normal Wi-Fi/Tailscale connection:

- cached gallery first content: under 200 ms;
- first uncached page metadata: p90 under 1 s;
- cached thumbnail: p90 under 300 ms;
- upload acknowledgement: under 500 ms, independent of AI processing;
- no data loss after an acknowledged upload (RPO 0 for accepted originals);
- recovery after process/server restart: under 15 minutes (RTO, periodic worker bound).

## Domain model

`DeviceSource` is a MediaStore bucket on one authorized device. Its stable identity is
`(device_id, volume_name, bucket_id, media_kind)`. Its display name and relative path
are labels, not authorization boundaries and not trusted server paths.

`LocalAsset` is identified by `(device_id, volume_name, media_store_id, generation)`.
It points to a `content://` URI and belongs to one `DeviceSource`.

`LibraryItem` is the server item. A successful upload links `LocalAsset` to a
`LibraryItem` using the server media ID and content hash. User-created albums remain
independent from device sources. A device folder is a derived view, never an album.

`SyncPolicy` contains:

- mode: `all` or `selected`;
- included source IDs when mode is `selected`;
- photos enabled;
- videos enabled;
- Wi-Fi-only and charging constraints.

The safe default is disabled. Enabling it requires media permission and an explicit
choice between all folders and selected folders.

## Data flow

```text
Android MediaStore
      │ scan by generation / source policy
      ▼
durable local queue ── resumable chunks ──► durable server upload row
      │                                         │
      │ local state                             │ atomic move after SHA-256
      ▼                                         ▼
unified local catalog ◄── change feed ── processing queue ──► private library
      │                                                       │
      └──────── local/server merge ───────────────────────────┘
```

The server stores originals under an operational path such as
`uploads/<device>/<source-token>/<year>/<month>/<content-hash>-<safe-name>`. Metadata is
authoritative; folder layout is only for backup and operator inspection. The
`source-token` is derived server-side from the opaque source ID, so an Android path can
never escape the account media root.

The gallery merges rows by server media ID first and content hash second. A local row
without a server link is device-only or pending. A server row without a readable local
URI is Iris-only. Opening an Iris-only item streams the original and optionally creates
an explicit offline copy; browsing never downloads every original.

## APIs and persistence

Upload initialization adds optional, backward-compatible fields:

```json
{
  "filename": "IMG_1234.jpg",
  "size": 5242880,
  "sha256": "...",
  "captured_at": "2026-09-10T12:00:00Z",
  "source": {
    "id": "external_primary:camera:images",
    "name": "Camera",
    "relative_path": "DCIM/Camera/",
    "volume": "external_primary",
    "media_store_id": "1234",
    "generation": 19,
    "media_kind": "image"
  }
}
```

The local queue persists that source snapshot with the URI. The server upload table
persists it before accepting bytes. After indexing, the library item receives
`origin_device_id`, `origin_source_id`, `origin_source_name`, and
`origin_relative_path`. A source-list endpoint groups by those fields and returns item
counts, enabling the three-click cleanup flow without manufacturing albums.

Idempotency uses the content hash for byte deduplication and the local asset identity
for scan deduplication. Upload state transitions are monotonic:

```text
device_only -> queued -> uploading -> backed_up -> processing -> ready
                                \-> duplicate              \-> failed_processing
```

## Performance, scaling, and operations

- SQLite remains a good fit because libraries are already physically sharded per
  account. Scale up the home server first; a future multi-node service shards by
  account ID and pins each account's writes to one worker.
- The Android catalog and Coil caches serve read-heavy browsing. Server thumbnails are
  immutable by content key and can be cached for a long time.
- Upload and AI processing are asynchronous. Processing must move from daemon threads
  to a durable per-account worker queue before claiming crash-safe server restarts.
- Backpressure limits active uploads per device and processing concurrency per account.
  Queue depth, age of oldest upload, bytes/second, processing duration, error rate,
  thumbnail p90, and gallery first-content p90 are the primary metrics.
- A single homelab server is intentionally not highly available. Originals, account
  databases, indexes, and the secret key need versioned backups to another device.
- Schema changes are additive and backward-compatible. Deploy server first, run its
  health and sync-contract checks, then roll out Android. Old clients omit `source` and
  continue under an `Unknown source` view.

## Trade-offs

Using only server directories would be easy to inspect but brittle after a rename and
unsafe if client paths were trusted. Using only albums would confuse physical origin
with intentional organization. Iris therefore keeps both an organized physical layout
and authoritative source metadata, while exposing source folders as derived views.

Client-side end-to-end encryption is incompatible with server-side thumbnails, face
recognition, OCR, and semantic search unless those computations move to the device.
That remains a separate private-vault mode rather than changing ordinary library sync.

## Design diagnostic

Score: **9/10 (7 of 8 rows)**.

- Requirements, estimates, database scaling, read caching, asynchronous queues,
  monitoring, and deployment strategy are defined.
- Failing row: every component redundant. A household Iris install has one server by
  design. The concrete fix is a second-node replicated deployment with automated
  failover; until that is justified, versioned off-host backups provide data recovery
  rather than high availability.

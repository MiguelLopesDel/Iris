# Server-authoritative library synchronization

<!-- status: proposed -->

For private multi-user Iris, each account's server library is the authoritative source of truth. Authorized devices keep a durable upload queue and a rebuildable cache, send new media to the server, and consume an incremental account change feed rather than mirroring the whole library. This avoids conflict-prone two-way filesystem synchronization, supports small phones and browsers, and keeps account isolation enforceable at the server boundary.

## Consequences

The mobile protocol needs device sessions, resumable uploads, item revisions and deletion tombstones. Upload acceptance must be separate from AI processing so a server with indexing paused can still safely receive media and report it as pending.

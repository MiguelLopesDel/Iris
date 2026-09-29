# Upload batching and multipart primitives

Research note, checked 2026-09-24. The vendor sources below document public APIs and user-visible behavior; they are not evidence of the internal implementations of Google Photos or iCloud Photos.

## Google Photos Library API: batch item creation, not packed bytes

**Documented facts.** The Library API upload flow has two steps: upload one media file's raw bytes and receive an upload token, then create the media item using that token. The upload guide recommends uploading bytes in parallel, while serializing `mediaItems.batchCreate` calls for a given user and putting as many items as possible into each call (maximum 50). Each item has a result; partial success can return `207 Multi-Status`. The API is restricted to app-created content for relevant operations since March 31, 2025, so it is not a general API for reading or managing a user's entire pre-existing Google Photos library. [Upload media](https://developers.google.com/photos/library/guides/upload-media) · [batchCreate reference](https://developers.google.com/photos/library/reference/rest/v1/mediaItems/batchCreate) · [Photos API updates](https://developers.google.com/photos/support/updates)

For resumable uploads, the API starts a session and allows either one request for a file or multiple chunks. Its guide says a single request is usually preferable when feasible because it uses fewer requests; for chunked transfer, chunks are sent serially at the server-provided granularity, as large as practical. Following an interruption, the client queries the server's confirmed upload position and resumes from there. Session URLs expire after seven days. [Resumable uploads](https://developers.google.com/photos/library/guides/resumable-uploads)

**Scope.** These are public Library API rules, not a description of the Google Photos mobile app's private synchronization pipeline.

## Apple iCloud Photos: documented product behavior only

Apple's public support documentation says iCloud Photos uploads photos and videos automatically when enabled, keeps edits and deletions reflected across devices, and offers visible sync status plus a pause control. It says upload time depends on collection size and internet speed and documents a priority option in newer OS versions. These pages do not specify an HTTP protocol, chunk format, request batching, or archive packaging; no claim about iCloud's internal transfer mechanism follows from them. [Set up and use iCloud Photos](https://support.apple.com/pt-br/108782) · [iPhone User Guide: sync photos and videos with iCloud](https://support.apple.com/guide/iphone/sync-photos-videos-icloud-iph961b96c4d/27/ios/27)

## Amazon S3 multipart upload: parallel parts of one object

**Documented facts.** S3 multipart upload divides one object into contiguous numbered parts. Parts can be uploaded independently and in any order; failed parts can be retried without retransmitting successful parts. S3 assembles the object in part-number order at completion, and the client must retain part numbers and returned ETags for that completion request. AWS recommends considering multipart for objects at least 100 MB, and says parallel parts can improve throughput on stable high-bandwidth links. Unfinished uploads must be completed or aborted to release stored parts. [S3 multipart overview](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)

This is a different primitive from batching many files into one archive: multipart is parallel transfer of byte ranges belonging to one logical object.

## Inferences for Iris

- Do not treat ZIP/tar packaging as the first throughput optimization. Google’s documented batching groups item-creation metadata after per-file byte uploads; S3's multipart pattern parallelizes parts of one object. Neither source recommends bundling unrelated photos into a single archive.
- Keep each photo/video independently addressable: separate durable job, source metadata, checksum, retry state, and terminal result. As a design inference, an archive otherwise couples retry and failure handling across members and requires a manifest plus server-side unpack/catalog work to recover per-item identity. JPEG/HEIC/MP4 are already compressed in common cases, so an archive is unlikely to materially reduce bytes, while adding CPU and staging I/O.
- If measurements show that one large video cannot use the available uplink while multiple independent items are unavailable, an S3-style per-media multipart protocol is a candidate: independently acknowledged ranges/parts followed by verified assembly. Iris's current [sync contract](../android-sync-contract.md) uses sequential server-confirmed offsets, so this would require a protocol and storage change; it should be benchmarked against the existing bounded parallel uploads before adoption.
- If many small items instead show control-request or catalog overhead, a batch-init or batch-finalize API is the closer analogue to Google `batchCreate`. Preserve an independent upload ID, quota reservation, checksum, source, and per-item result for every file, and handle partial success explicitly. This can reduce control-plane round trips; it cannot increase the network's available byte rate by itself.

These Iris recommendations are engineering inferences from the cited vendor primitives and the current sync contract, not vendor guarantees.

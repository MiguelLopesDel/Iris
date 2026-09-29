# Consumer photo-library sync: Google Photos and iCloud Photos

Research note, checked 2026-09-25. This compares consumer-facing behavior described in first-party product documentation. It does **not** describe private client/server implementations. “Not documented” means the sources linked here do not specify the behavior; it is not a claim that the products lack such behavior.

## Google Photos

### Enrollment, account, and library

Backup is an opt-in setting. Once enabled, Photos can automatically save device photos and videos to the selected Google Account. Google documents backing up to only one Google Account at a time. The app still displays media stored locally on the device regardless of the signed-in Google Account, so seeing an item in the app does not prove it is backed up to the currently selected account. [Google Photos Help: Back up photos & videos](https://support.google.com/photos/answer/6193313?co=GENIE.Platform%3DAndroid&hl=en)

The camera is not the only possible source: the user can select device folders, enable all folders, or select individual folders. If “Back up all device folders” is enabled, new device folders are automatically included. Google separately describes hiding device-folder content from the Photos view; this is a presentation choice, not the same as excluding a folder from backup. [Google Photos Help: device folders and backup settings](https://support.google.com/photos/answer/6193313?co=GENIE.Platform%3DAndroid&hl=en)

### Progress, network, power, and storage

Google exposes a library-level status (including preparing, backing up, complete, and paused) and a remaining-items count. Per-item states distinguish uploading, queued/paused, already backed up, and permanent failure. The docs also describe manual backup for one item or a selection. [Google Photos Help: progress and item status](https://support.google.com/photos/answer/6193313?co=GENIE.Platform%3DAndroid&hl=en)

For mobile data, the user can set a daily automatic-backup limit, disable video backup over data, disable roaming backup, and—in some supported plans/regions—use unrestricted mobile data. Google cautions that Android battery optimization can make backup work poorly. Google says completion time depends on connection, upload size, and other conditions; these help pages do not promise when a background transfer will run or explain its retry, chunk-resume, or scheduling algorithms. [Google Photos Help: mobile data and battery optimization](https://support.google.com/photos/answer/6193313?co=GENIE.Platform%3DAndroid&hl=en)

Google Account storage is shared by Photos, Drive, and Gmail. The same help page lists media eligibility/size limits, including photos up to 200 MB or 200 MP and videos up to 10 GB. [Google Photos Help: requirements and storage](https://support.google.com/photos/answer/6193313?co=GENIE.Platform%3DAndroid&hl=en)

### Edits and deletion

Google says automatic sync makes backed-up photos and edits available on other signed-in devices. Its user guidance says deleting an item in Google Photos can delete both the Google Photos copy and the device copy; when backed up, deletion propagates to devices with backup enabled. Backed-up items remain in Trash for 30 days; the page also says unbacked items deleted from Android 11+ devices remain in Trash for 30 days. “Delete from device” is a separate action for removing the local copy while retaining the cloud item. There is also an “Undo backup” operation that removes the current device’s backed-up items from Google Photos while leaving them on the device and turning backup off; Google warns that another device with backup enabled can upload those items again. [Google Photos Help: sync, deletion, and undo backup](https://support.google.com/photos/answer/6128858?co=GENIE.Platform%3DAndroid&hl=en) [Google Photos Help: backup behavior](https://support.google.com/photos/answer/6193313?co=GENIE.Platform%3DAndroid&hl=en)

The consumer help pages explain propagation and recovery, but do not specify how the app identifies the same media across devices, resolves simultaneous edits, deduplicates, or orders/retries individual upload requests.

## Apple iCloud Photos

### Enrollment, account, and library

The user enables “Sync this iPhone” in iCloud Photos. Apple describes this as synchronizing the photo library: photos and videos upload automatically in original format and full resolution, and changes made to the collection on one device are reflected on other devices signed into the same Apple Account with iCloud Photos enabled. [iPhone User Guide: Back up and sync with iCloud](https://support.apple.com/guide/iphone/sync-photos-videos-icloud-iph961b96c4d/27/ios/27)

With “Optimize iPhone Storage,” full-resolution originals remain in iCloud while storage-saving versions are kept on the iPhone; Apple says this option is on by default. The alternative is to download and keep originals on the device. [iPhone User Guide: Optimize iPhone Storage](https://support.apple.com/guide/iphone/sync-photos-videos-icloud-iph961b96c4d/27/ios/27)

Apple also documents Personal and Shared Photo Libraries. Moving items to the Shared Library means they no longer appear in the Personal Library view. This is a library/view distinction; it does not mean the item has stopped syncing. [Apple Support: iCloud Photos sync status and Shared Library](https://support.apple.com/en-us/101559)

### Progress, network, power, and storage

Photos surfaces a library sync status and last-sync time. The iPhone guide documents visual indicators for active uploads, paused uploads, storage warnings/full state, and shared-library notifications; tapping the account/profile control reveals details. [iPhone User Guide: check iCloud Photos status](https://support.apple.com/guide/iphone/sync-photos-videos-icloud-iph961b96c4d/27/ios/27)

Apple recommends connecting to power and Wi-Fi and allowing a large library to sync uninterrupted overnight; it notes that large files may take longer. Documented pause reasons include Low Data Mode, Low Power Mode, system/battery optimization, poor network, battery below 20%, overheating, and full iCloud storage. Depending on the reason, Apple says to wait, charge/cool the device, free or purchase storage, or tap “Sync Now.” Apple explicitly advises not to turn iCloud Photos off as a troubleshooting step because that restarts syncing. [Apple Support: If your iCloud Photos aren't syncing](https://support.apple.com/en-us/101559)

“Sync Immediately” temporarily prioritizes items added to the library after it is enabled, lasts for the day, and can increase battery and cellular-data use; it pauses under Low Power Mode or Low Data Mode. This documents a user-visible priority control, not a guaranteed completion deadline. [iPhone User Guide: Sync iCloud Photos immediately](https://support.apple.com/guide/iphone/sync-photos-videos-icloud-iph961b96c4d/27/ios/27)

### Edits and deletion

Apple describes iCloud Photos as synchronization, not just one-way backup: changes to the photo collection propagate to the other devices. Deleting or hiding an item in Photos with iCloud Photos enabled applies across devices signed into the same Apple Account. Deleted items are kept in Recently Deleted for 30 days, during which they can be recovered or permanently deleted. [iPhone User Guide: sync behavior](https://support.apple.com/guide/iphone/sync-photos-videos-icloud-iph961b96c4d/27/ios/27) [iPhone User Guide: delete, hide, and recover](https://support.apple.com/guide/iphone/delete-or-hide-photos-and-videos-iphb4defbde9/27/ios/27)

The public consumer pages do not specify iCloud Photos’ upload queue, wire protocol, retry/resume strategy, duplicate matching, or conflict-resolution algorithm for simultaneous edits. The documented “changes are reflected” statement establishes user-visible propagation, not internal merge semantics.

## What the developer APIs reveal (and do not reveal)

Google’s separate Photos Library API documents a concrete two-step upload flow: transfer a file’s bytes to obtain an upload token, then create the library item from that token. Its guidance says raw byte uploads may run in parallel, while `mediaItems.batchCreate` calls should be serialized per user and can create up to 50 items per call; the response reports an outcome for each item and can indicate partial success. This is useful evidence about Google’s public integration API, **not** evidence that the consumer Android app uses this exact protocol or concurrency strategy. The API is also now limited to app-created content for relevant operations, so it is not a general-purpose interface to a user’s full existing Photos library. [Google Photos Library API: upload media](https://developers.google.com/photos/library/legacy/guides/upload-media) [Google Photos APIs updates](https://developers.google.com/photos/support/updates)

Apple’s PhotoKit Background Resource Upload extension is another public developer feature, but it uploads a third-party app’s Photos-library resources to that developer’s server. Apple documents OS scheduling around network, power, and device conditions, one job per asset resource, and server capability negotiation for resumable upload. It is explicitly an API for apps/extensions; it does **not** describe iCloud Photos’ own upload implementation. [Apple Developer: uploading asset resources in the background](https://developer.apple.com/documentation/photokit/uploading-asset-resources-in-the-background)

## Comparison and relevance to Iris

| Topic | Google Photos (documented) | iCloud Photos (documented) |
|---|---|---|
| User’s mental model | Opt-in backup to one selected account, with a local-device view alongside cloud content. | One library synchronized among devices signed into the same Apple Account. |
| Source selection | Per-device-folder selection, including an option for future folders. | Sync this iPhone applies to the Photos library; the cited guide does not describe Android-style per-folder backup selection. |
| Status visibility | Remaining-item count, library states, and per-item backup/queue/failure indicators. | Library status, active/paused visual states, reason details, and last-sync time. |
| Resource controls | Mobile-data budget, video/roaming switches, battery-optimization caveat, shared Google storage. | Wi-Fi/power recommendation, status-specific pause reasons, storage optimization, shared iCloud capacity. |
| Delete behavior | Delete can affect local and cloud copies; “Delete from device” and “Undo backup” are distinct operations. | Delete/hide propagates across the synchronized library; Recently Deleted provides a 30-day recovery window. |
| Internal transfer/conflict rules | Not specified by cited consumer docs. | Not specified by cited consumer docs. |

**Inference for Iris, not a product requirement or code recommendation:** these two products demonstrate distinct and understandable user contracts. Google’s documented contract emphasizes choosing which local folders to back up and distinguishing “on this phone” from “backed up to this account.” Apple’s emphasizes one synchronized library, optimization of local originals, and clear status/reasons. Both make sync status visible and distinguish cloud/library state from device storage. They do not provide public evidence for copying either company’s internal transport or conflict-resolution design. Iris should not imply that backup and bidirectional synchronization are the same operation; the desired semantics must be stated explicitly in Iris’s own product contract.

## Scope and source limits

Sources are first-party Google Photos Help and Apple Support/User Guide pages linked above. They describe supported consumer behavior, settings, and user-visible states. They are not engineering documentation for the production apps. No claim here about upload concurrency, chunk size, durable queue format, polling cadence, background scheduler, dedupe key, server data model, or conflict algorithm should be read as a description of Google or Apple internals.

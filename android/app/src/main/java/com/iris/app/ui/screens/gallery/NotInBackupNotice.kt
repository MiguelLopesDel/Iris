package com.iris.app.ui.screens.gallery

import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.res.stringResource
import com.iris.app.IrisApplication
import com.iris.app.R
import com.iris.app.data.repository.ServerSettingsRepository.AccountSyncSettings
import com.iris.app.data.sync.DeviceFolder
import com.iris.app.data.sync.DeviceFolders
import com.iris.app.data.sync.MediaSyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The notice for a device item the backup does not cover, or null when it is
 * covered (or nobody is signed in). Its action includes the folder, and the
 * media kind when that is what was left out, then starts a sync.
 */
@Composable
fun rememberNotInBackupNotice(application: IrisApplication, mediaUri: String): NotInBackupNotice? {
    val accountKey by application.credentialsStore.accountIdentity.collectAsState()
    val settings by remember(accountKey) {
        accountKey?.let(application.settingsRepository::syncSettingsForAccount) ?: flowOf(null)
    }.collectAsState(initial = null)
    val folder by produceState<DeviceFolder?>(initialValue = null, mediaUri) {
        value = withContext(Dispatchers.IO) { DeviceFolders.of(application.contentResolver, Uri.parse(mediaUri)) }
    }
    val scope = rememberCoroutineScope()

    val key = accountKey ?: return null
    val current = settings ?: return null
    val item = folder ?: return null
    if (DeviceFolders.policyOf(current).includes(item.sourceId, item.mediaKind)) return null

    val kindLeftOut = !kindIncluded(current, item.mediaKind)
    val folderLeftOut = current.sourceMode != "all" && item.sourceId !in current.selectedSourceIds
    val message = if (kindLeftOut && !folderLeftOut) {
        stringResource(
            R.string.local_media_kind_not_in_backup,
            stringResource(if (item.mediaKind == "video") R.string.local_media_kind_videos else R.string.local_media_kind_photos),
        )
    } else {
        stringResource(R.string.local_media_not_in_backup, item.name.ifBlank { item.relativePath })
    }
    return NotInBackupNotice(message) {
        scope.launch {
            val repository = application.settingsRepository
            if (folderLeftOut) repository.updateSelectedSourceIds(key, current.selectedSourceIds + item.sourceId)
            if (kindLeftOut) {
                if (item.mediaKind == "video") repository.updateSyncVideosEnabled(key, true)
                else repository.updateSyncImagesEnabled(key, true)
            }
            MediaSyncWorker.enqueueImmediate(application)
        }
    }
}

private fun kindIncluded(settings: AccountSyncSettings, mediaKind: String): Boolean =
    if (mediaKind == "video") settings.videosEnabled else settings.imagesEnabled

package com.iris.app.ui.components

import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.platform.LocalContext
import com.iris.app.IrisApplication
import com.iris.app.R
import com.iris.app.data.MediaSharer
import kotlinx.coroutines.launch

/**
 * "Compartilhar", shared by every viewer: fetches the item and opens the
 * system share sheet with the file itself. Returns `share(url, fileName)`,
 * where `url` is absolute.
 */
@Composable
fun rememberMediaShare(onNotice: (String) -> Unit): (String, String) -> Unit {
    val context = LocalContext.current
    val apiClient = (context.applicationContext as IrisApplication).apiClient
    val scope = rememberCoroutineScope()
    return { url, fileName ->
        onNotice(context.getString(R.string.share_preparing))
        scope.launch {
            when (val result = MediaSharer(context, apiClient).prepare(url, fileName)) {
                is MediaSharer.Result.Ready -> context.startActivity(result.intent)
                is MediaSharer.Result.Failed -> onNotice(context.getString(R.string.share_failed, result.reason))
            }
        }
    }
}

/** Shares several library items in one go; each pair is (media URL, file name). */
@Composable
fun rememberMediaShareAll(onNotice: (String) -> Unit): (List<Pair<String, String>>) -> Unit {
    val context = LocalContext.current
    val apiClient = (context.applicationContext as IrisApplication).apiClient
    val scope = rememberCoroutineScope()
    return { items ->
        onNotice(context.getString(R.string.share_preparing))
        scope.launch {
            when (val result = MediaSharer(context, apiClient).prepareAll(items)) {
                is MediaSharer.Result.Ready -> context.startActivity(result.intent)
                is MediaSharer.Result.Failed -> onNotice(context.getString(R.string.share_failed, result.reason))
            }
        }
    }
}

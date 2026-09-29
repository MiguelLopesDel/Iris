package com.iris.app.ui.components

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import com.iris.app.IrisApplication
import com.iris.app.data.MediaDownloader
import kotlinx.coroutines.launch

/**
 * "Baixar", shared by every viewer: asks for storage access first where the
 * platform needs it (Android 8-9, see [MediaDownloader.missingPermissions]) and
 * reports the outcome through [onNotice]. Returns `download(url, fileName)`,
 * where `url` is absolute.
 */
@Composable
fun rememberMediaDownload(onNotice: (String) -> Unit): (String, String) -> Unit {
    val context = LocalContext.current
    val apiClient = (context.applicationContext as IrisApplication).apiClient
    val scope = rememberCoroutineScope()
    var pending by remember { mutableStateOf<Pair<String, String>?>(null) }

    fun start(url: String, fileName: String) {
        onNotice("Baixando…")
        scope.launch {
            val result = MediaDownloader(context, apiClient).download(url, fileName)
            onNotice(
                when (result) {
                    is MediaDownloader.Result.Saved -> "Salvo em Downloads: ${result.displayName}"
                    is MediaDownloader.Result.Failed -> "Falha ao baixar: ${result.reason}"
                }
            )
        }
    }

    val permission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        val request = pending ?: return@rememberLauncherForActivityResult
        pending = null
        if (grants.values.all { it }) start(request.first, request.second)
        else onNotice("Sem permissão de armazenamento, não dá para baixar")
    }

    return { url, fileName ->
        val missing = MediaDownloader.missingPermissions(context)
        if (missing.isEmpty()) {
            start(url, fileName)
        } else {
            pending = url to fileName
            permission.launch(missing.toTypedArray())
        }
    }
}

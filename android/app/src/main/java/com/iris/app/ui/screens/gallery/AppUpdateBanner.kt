package com.iris.app.ui.screens.gallery

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.iris.app.R
import com.iris.app.data.update.AppRelease
import com.iris.app.data.update.AppUpdates
import kotlinx.coroutines.launch

/** A newer app release at the top of the gallery: install it, or hide it until the next one. */
@Composable
fun AppUpdateBanner(release: AppRelease, updates: AppUpdates, modifier: Modifier = Modifier) {
    val scope = rememberCoroutineScope()
    var downloading by remember(release.version) { mutableStateOf(false) }
    var failed by remember(release.version) { mutableStateOf(false) }

    Card(modifier = modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                stringResource(R.string.app_update_available, release.version),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                stringResource(if (failed) R.string.app_update_failed else R.string.app_update_hint),
                style = MaterialTheme.typography.bodyMedium,
                color = if (failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    enabled = !downloading,
                    onClick = {
                        downloading = true
                        failed = false
                        scope.launch {
                            runCatching { updates.download(release) }
                                .onSuccess { updates.install(it) }
                                .onFailure { failed = true }
                            downloading = false
                        }
                    },
                ) {
                    Text(stringResource(if (downloading) R.string.app_update_downloading else R.string.app_update_install))
                }
                TextButton(enabled = !downloading, onClick = { updates.dismiss(release) }) {
                    Text(stringResource(R.string.app_update_not_now))
                }
            }
        }
    }
}

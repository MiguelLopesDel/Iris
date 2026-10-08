package com.iris.app.ui.screens.sync

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.clickable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.CloudDone
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.HourglassEmpty
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.CloudConnectionState
import com.iris.app.data.model.UploadJobState
import com.iris.app.data.model.UploadQueueSummary
import com.iris.app.R
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.CloudSyncNotice
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisDarkBg
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisDarkSurfaceBright
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.ui.theme.IrisViolet

private enum class MediaPermissionFollowUp {
    DiscoverFolders,
    EnableAutoBackup,
    EnableAllFolders
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SyncScreen(
    viewModel: SyncViewModel,
    onConfigureServer: () -> Unit
) {
val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var mediaLibraryAccess by remember(context) {
        mutableStateOf(currentMediaLibraryAccess(context))
    }
    var permissionFollowUp by remember { mutableStateOf(MediaPermissionFollowUp.DiscoverFolders) }
    var showDeviceSourcePicker by remember { mutableStateOf(false) }
    var backgroundAccess by remember(context) { mutableStateOf(BackgroundSyncAccess.current(context)) }
    val notificationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { backgroundAccess = BackgroundSyncAccess.current(context) }
    val pickerVisible = rememberUpdatedState(showDeviceSourcePicker)
    val mediaPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        mediaLibraryAccess = classifyMediaLibraryAccess(
            sdk = Build.VERSION.SDK_INT,
            imageGranted = grants[Manifest.permission.READ_MEDIA_IMAGES] == true,
            videoGranted = grants[Manifest.permission.READ_MEDIA_VIDEO] == true,
            selectedMediaGranted = grants[Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED] == true,
            legacyStorageGranted = grants[Manifest.permission.READ_EXTERNAL_STORAGE] == true
        )
        if (grants.values.any { it }) {
            viewModel.discoverSources()
            if (mediaLibraryAccess == MediaLibraryAccess.LIMITED) {
                showDeviceSourcePicker = true
            }
            when (permissionFollowUp) {
                MediaPermissionFollowUp.DiscoverFolders ->
                    viewModel.finishPendingBackupSetupIfScopeChosen(context)
                MediaPermissionFollowUp.EnableAutoBackup ->
                    viewModel.setAutoBackupEnabled(true, context)
                MediaPermissionFollowUp.EnableAllFolders ->
                    if (mediaLibraryAccess == MediaLibraryAccess.FULL) {
                        viewModel.enableBackupForAllFolders(context)
                    }
            }
        } else {
            viewModel.showMediaPermissionRequired()
        }
    }
    DisposableEffect(lifecycleOwner, context) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                mediaLibraryAccess = currentMediaLibraryAccess(context)
                // The battery exemption is granted in a system screen; re-read it on return.
                backgroundAccess = BackgroundSyncAccess.current(context)
                if (pickerVisible.value) viewModel.discoverSources()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    val requestMediaPermission: (MediaPermissionFollowUp) -> Unit = { followUp ->
        permissionFollowUp = followUp
        mediaPermissionLauncher.launch(mediaPermissionsForSdk(Build.VERSION.SDK_INT))
    }
    val openDeviceSourcePicker: () -> Unit = {
        showDeviceSourcePicker = true
        requestMediaPermission(MediaPermissionFollowUp.DiscoverFolders)
    }

    if (uiState.isLoggedIn && uiState.backupSetupPromptReady &&
        !uiState.backupSetupPromptAnswered && !uiState.autoBackupEnabled
    ) {
        AlertDialog(
            onDismissRequest = { viewModel.answerBackupSetupPrompt(configureFolders = false) },
            title = { Text(stringResource(R.string.sync_backup_setup_title)) },
            text = { Text(stringResource(R.string.sync_backup_setup_message)) },
            confirmButton = {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.End
                ) {
                    TextButton(onClick = {
                        viewModel.answerBackupSetupPrompt(configureFolders = false) {
                            requestMediaPermission(MediaPermissionFollowUp.EnableAllFolders)
                        }
                    }) {
                        Text(stringResource(R.string.sync_backup_all_folders_action))
                    }
                    TextButton(onClick = {
                        viewModel.answerBackupSetupPrompt(configureFolders = true) {
                            openDeviceSourcePicker()
                        }
                    }) {
                        Text(stringResource(R.string.sync_backup_choose_folders_action))
                    }
                    TextButton(onClick = {
                        viewModel.answerBackupSetupPrompt(configureFolders = false)
                    }) {
                        Text(stringResource(R.string.sync_backup_not_now_action))
                    }
                }
            }
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "Sincronização & Backup",
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold
                    )
                },
                actions = {
                    IconButton(onClick = onConfigureServer) {
                        Icon(imageVector = Icons.Default.Dns, contentDescription = "Configurar servidor")
                    }
                    IconButton(onClick = { viewModel.loadQueue() }) {
                        Icon(imageVector = Icons.Default.Refresh, contentDescription = "Atualizar fila")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisDarkBg)
            )
        },
        containerColor = IrisDarkBg
    ) { paddingValues ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            if (uiState.isLoggedIn && (
                    uiState.cloudSyncStatus.connectionState == CloudConnectionState.OFFLINE ||
                        uiState.cloudSyncStatus.syncError != null || uiState.queueRefreshFailed
                    )
            ) {
                item {
                    CloudSyncNotice(
                        status = uiState.cloudSyncStatus,
                        queueRefreshFailed = uiState.queueRefreshFailed,
                    )
                }
            }

            // ── Section 1: Authentication / Device Identity ──────────────────────
            item {
                if (!uiState.isLoggedIn) {
                    Card(
                        shape = RoundedCornerShape(16.dp),
                        colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    imageVector = Icons.Default.Lock,
                                    contentDescription = null,
                                    tint = IrisAccentLime
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = "Conectar Dispositivo",
                                    fontSize = 17.sp,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = "Autentique seu aparelho para iniciar o backup contínuo de mídias.",
                                fontSize = 13.sp,
                                color = IrisTextSoft
                            )

                            Spacer(modifier = Modifier.height(12.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text("Servidor", fontSize = 12.sp, color = IrisTextMuted)
                                    Text(
                                        text = uiState.serverUrl.ifBlank { "Endereço não configurado" },
                                        fontSize = 13.sp,
                                        color = IrisTextSoft,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis
                                    )
                                }
                                TextButton(onClick = onConfigureServer) {
                                    Text(if (uiState.serverUrl.isBlank()) "Configurar" else "Trocar")
                                }
                            }

                            Spacer(modifier = Modifier.height(14.dp))

                            OutlinedTextField(
                                value = uiState.loginUsernameInput,
                                onValueChange = { viewModel.onUsernameChange(it) },
                                label = { Text("Nome de usuário") },
                                leadingIcon = { Icon(Icons.Default.Person, contentDescription = null) },
                                singleLine = true,
                                shape = RoundedCornerShape(12.dp),
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedContainerColor = IrisDarkSurfaceBright,
                                    unfocusedContainerColor = IrisDarkSurfaceBright,
                                    focusedBorderColor = IrisAccentLime
                                ),
                                modifier = Modifier.fillMaxWidth()
                            )

                            Spacer(modifier = Modifier.height(10.dp))

                            OutlinedTextField(
                                value = uiState.loginPasswordInput,
                                onValueChange = { viewModel.onPasswordChange(it) },
                                label = { Text("Senha") },
                                leadingIcon = { Icon(Icons.Default.Lock, contentDescription = null) },
                                visualTransformation = PasswordVisualTransformation(),
                                singleLine = true,
                                shape = RoundedCornerShape(12.dp),
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedContainerColor = IrisDarkSurfaceBright,
                                    unfocusedContainerColor = IrisDarkSurfaceBright,
                                    focusedBorderColor = IrisAccentLime
                                ),
                                modifier = Modifier.fillMaxWidth()
                            )

                            Spacer(modifier = Modifier.height(10.dp))

                            OutlinedTextField(
                                value = uiState.deviceNameInput,
                                onValueChange = { viewModel.onDeviceNameChange(it) },
                                label = { Text("Nome do Aparelho") },
                                leadingIcon = { Icon(Icons.Default.PhoneAndroid, contentDescription = null) },
                                singleLine = true,
                                shape = RoundedCornerShape(12.dp),
                                colors = OutlinedTextFieldDefaults.colors(
                                    focusedContainerColor = IrisDarkSurfaceBright,
                                    unfocusedContainerColor = IrisDarkSurfaceBright,
                                    focusedBorderColor = IrisAccentLime
                                ),
                                modifier = Modifier.fillMaxWidth()
                            )

                            if (uiState.loginError != null) {
                                Spacer(modifier = Modifier.height(10.dp))
                                Text(
                                    text = uiState.loginError ?: "",
                                    color = IrisDanger,
                                    fontSize = 13.sp
                                )
                            }

                            Spacer(modifier = Modifier.height(16.dp))

                            Button(
                                onClick = { viewModel.loginDevice() },
                                enabled = !uiState.isLoggingIn && uiState.serverUrl.isNotBlank() &&
                                    uiState.loginUsernameInput.isNotBlank() && uiState.loginPasswordInput.isNotBlank(),
                                shape = RoundedCornerShape(12.dp),
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = IrisAccentLime,
                                    contentColor = IrisAccentInk
                                ),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(48.dp)
                            ) {
                                if (uiState.isLoggingIn) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(20.dp),
                                        color = IrisAccentInk,
                                        strokeWidth = 2.dp
                                    )
                                    Spacer(modifier = Modifier.width(8.dp))
                                    Text("Autenticando…", fontWeight = FontWeight.Bold)
                                } else {
                                    Text("Entrar e Sincronizar", fontWeight = FontWeight.Bold)
                                }
                            }
                        }
                    }
                } else {
                    // Logged in device info card
                    Card(
                        shape = RoundedCornerShape(16.dp),
                        colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Box(
                                        modifier = Modifier
                                            .size(40.dp)
                                            .background(IrisDarkSurfaceBright, CircleShape),
                                        contentAlignment = Alignment.Center
                                    ) {
                                        Icon(
                                            imageVector = Icons.Default.AccountCircle,
                                            contentDescription = null,
                                            tint = IrisAccentLime
                                        )
                                    }
                                    Spacer(modifier = Modifier.width(12.dp))
                                    Column {
                                        Text(
                                            text = uiState.username,
                                            fontWeight = FontWeight.Bold,
                                            fontSize = 16.sp
                                        )
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Box(
                                                modifier = Modifier
                                                    .size(6.dp)
                                                    .background(IrisAccentLime, CircleShape)
                                            )
                                            Spacer(modifier = Modifier.width(6.dp))
                                            Text(
                                                text = "Dispositivo Conectado",
                                                fontSize = 12.sp,
                                                color = IrisTextSoft
                                            )
                                        }
                                    }
                                }
                                OutlinedButton(
                                    onClick = { viewModel.logoutDevice() },
                                    shape = RoundedCornerShape(8.dp)
                                ) {
                                    Text("Desconectar", fontSize = 12.sp, color = IrisDanger)
                                }
                            }

                            if (uiState.deviceId.isNotBlank()) {
                                Spacer(modifier = Modifier.height(10.dp))
                                Text(
                                    text = "ID: ${uiState.deviceId.take(12)}…",
                                    fontSize = 11.sp,
                                    color = IrisTextMuted
                                )
                            }
                        }
                    }
                }
            }

            // ── Section 2: Sync Actions & Constraints ─────────────────────────────
            item {
                Card(
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = "Status da Sincronização",
                                    fontWeight = FontWeight.SemiBold,
                                    fontSize = 15.sp
                                )
                                val activity = SyncActivity.of(uiState)
                                val scan = uiState.scanProgress
                                Text(
                                    text = when (activity) {
                                        SyncActivity.SCANNING -> stringResource(
                                            R.string.sync_status_scanning,
                                            scan?.examined ?: 0,
                                            scan?.total ?: 0,
                                        )
                                        SyncActivity.SCANNING_AND_UPLOADING -> stringResource(
                                            R.string.sync_status_scanning_and_uploading,
                                            scan?.examined ?: 0,
                                            scan?.total ?: 0,
                                        )
                                        SyncActivity.UPLOADING -> stringResource(R.string.sync_status_uploading)
                                        SyncActivity.RETRY_PENDING -> stringResource(R.string.sync_status_retry_pending)
                                        SyncActivity.IDLE -> stringResource(R.string.sync_status_idle)
                                    },
                                    fontSize = 12.sp,
                                    color = if (activity.isRunning) IrisAccentLime else IrisTextSoft
                                )
                            }

                            Button(
                                onClick = { viewModel.triggerManualSync(context) },
                                enabled = uiState.isLoggedIn && !SyncActivity.of(uiState).isRunning,
                                shape = RoundedCornerShape(12.dp),
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = IrisAccentLime,
                                    contentColor = IrisAccentInk
                                )
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Sync,
                                    contentDescription = null,
                                    modifier = Modifier.size(16.dp)
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = stringResource(
                                        if (SyncActivity.of(uiState) == SyncActivity.RETRY_PENDING) R.string.sync_action_retry_now
                                        else R.string.sync_action_sync
                                    ),
                                    fontWeight = FontWeight.Bold,
                                    fontSize = 13.sp
                                )
                            }
                        }

                        if (uiState.queueTotal > 0) {
                            Spacer(modifier = Modifier.height(12.dp))
                            BackupCounters(uiState.queueCounts)
                        }

                        val backupProgress = BackupProgress.of(
                            UploadQueueSummary.from(uiState.queueCounts),
                            uiState.scanProgress,
                            SyncActivity.of(uiState).isRunning,
                        )
                        if (backupProgress != null) {
                            Spacer(modifier = Modifier.height(12.dp))
                            LinearProgressIndicator(
                                progress = { backupProgress },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(6.dp),
                                color = IrisAccentLime,
                                trackColor = IrisDarkSurfaceBright
                            )
                        }

                        if (uiState.uploadSpeed.runNumber > 0L) {
                            Spacer(modifier = Modifier.height(12.dp))
                            UploadSpeedPanel(uiState.uploadSpeed, uiState.remainingUploadBytes)
                        }

                        if (uiState.isLoggedIn) {
                            Spacer(modifier = Modifier.height(12.dp))
                            ServerSpeedTestPanel(
                                running = uiState.isSpeedTestRunning,
                                results = uiState.speedTestResults,
                                error = uiState.speedTestError,
                                enabled = !uiState.isSyncing,
                                onRun = viewModel::runSpeedTest
                            )
                        }

                        Spacer(modifier = Modifier.height(16.dp))
                        HorizontalDivider(color = IrisDarkSurfaceBright)
                        Spacer(modifier = Modifier.height(12.dp))

                        // Preferences switches
                        PreferenceSwitch(
                            title = "Backup automático contínuo",
                            subtitle = "Descobre novas fotos e vídeos via MediaStore",
                            checked = uiState.autoBackupEnabled,
                            onCheckedChange = { enabled ->
                                if (enabled) requestMediaPermission(MediaPermissionFollowUp.EnableAutoBackup)
                                else viewModel.setAutoBackupEnabled(false, context)
                            }
                        )

                        Spacer(modifier = Modifier.height(8.dp))

                        PreferenceSwitch(
                            title = stringResource(R.string.sync_all_folders),
                            subtitle = if (uiState.sourceMode == "all") {
                                stringResource(R.string.sync_all_folders_on)
                            } else {
                                stringResource(R.string.sync_all_folders_off)
                            },
                            checked = uiState.sourceMode == "all",
                            onCheckedChange = { enabled ->
                                if (enabled && uiState.backupSetupPending) {
                                    requestMediaPermission(MediaPermissionFollowUp.EnableAllFolders)
                                } else {
                                    viewModel.setSourceMode(if (enabled) "all" else "selected", context)
                                }
                            }
                        )

                        Spacer(modifier = Modifier.height(8.dp))

                        PreferenceSwitch(
                            title = stringResource(R.string.sync_photos),
                            subtitle = stringResource(R.string.sync_photos_description),
                            checked = uiState.syncImagesEnabled,
                            onCheckedChange = viewModel::setSyncImagesEnabled
                        )

                        Spacer(modifier = Modifier.height(8.dp))

                        PreferenceSwitch(
                            title = stringResource(R.string.sync_videos),
                            subtitle = stringResource(R.string.sync_videos_description),
                            checked = uiState.syncVideosEnabled,
                            onCheckedChange = viewModel::setSyncVideosEnabled
                        )

                        if (uiState.sourceMode == "selected") {
                            Spacer(modifier = Modifier.height(10.dp))
                            OutlinedButton(
                                onClick = openDeviceSourcePicker,
                                enabled = !uiState.isDiscoveringSources,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Icon(Icons.Default.FolderOpen, contentDescription = null)
                                Spacer(modifier = Modifier.width(10.dp))
                                Column(modifier = Modifier.weight(1f), horizontalAlignment = Alignment.Start) {
                                    Text(stringResource(R.string.sync_folders_section), fontWeight = FontWeight.SemiBold)
                                    val sourceSummary = when {
                                        uiState.isDiscoveringSources ->
                                            stringResource(R.string.sync_folders_loading)
                                        uiState.availableSources.isEmpty() ->
                                            stringResource(R.string.sync_folders_not_loaded)
                                        else -> stringResource(
                                            R.string.sync_folders_selected_count,
                                            uiState.selectedSourceIds.size,
                                            uiState.availableSources.size
                                        )
                                    }
                                    Text(sourceSummary, fontSize = 12.sp, color = IrisTextSoft)
                                }
                                if (uiState.isDiscoveringSources) {
                                    CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                                } else {
                                    Text(stringResource(R.string.sync_source_picker_edit), fontWeight = FontWeight.SemiBold)
                                }
                            }
                        }

                        uiState.sourceDiscoveryError?.let { message ->
                            if (!showDeviceSourcePicker) {
                                Spacer(modifier = Modifier.height(8.dp))
                                Text(
                                    if (message == "MEDIA_PERMISSION_REQUIRED") {
                                        stringResource(R.string.sync_media_permission_required)
                                    } else {
                                        message
                                    },
                                    color = IrisDanger,
                                    fontSize = 12.sp
                                )
                            }
                        }

                        if (uiState.backupSetupPending) {
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                text = stringResource(R.string.sync_backup_choose_scope_hint),
                                color = IrisTextSoft,
                                fontSize = 12.sp
                            )
                        }

                        Spacer(modifier = Modifier.height(8.dp))

                        PreferenceSwitch(
                            title = "Apenas em redes Wi-Fi",
                            subtitle = "Economiza os dados móveis do plano",
                            checked = uiState.syncWifiOnly,
                            onCheckedChange = { viewModel.setSyncWifiOnly(it) }
                        )

                        Spacer(modifier = Modifier.height(8.dp))

                        PreferenceSwitch(
                            title = "Apenas enquanto carrega",
                            subtitle = "Evita consumo excessivo de bateria",
                            checked = uiState.syncChargingOnly,
                            onCheckedChange = { viewModel.setSyncChargingOnly(it) }
                        )
                    }
                }
            }

            if (uiState.isLoggedIn && uiState.autoBackupEnabled && !backgroundAccess.complete) {
                item {
                    BackgroundSyncAccessCard(
                        access = backgroundAccess,
                        onAllowBattery = { requestBatteryExemption(context) },
                        onAllowNotifications = {
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                            }
                        }
                    )
                }
            }

            // ── Section 3: Run history (survives the app being closed) ───────────
            item {
                SectionToggle(
                    title = stringResource(R.string.sync_history_section),
                    subtitle = if (uiState.syncRuns.isEmpty()) {
                        stringResource(R.string.sync_history_empty)
                    } else {
                        stringResource(R.string.sync_history_subtitle, uiState.syncRuns.size)
                    },
                    expanded = uiState.isHistoryExpanded,
                    onToggle = viewModel::toggleHistory
                )
            }
            if (uiState.isHistoryExpanded) {
                items(uiState.syncRuns, key = { "run-${it.id}" }) { run ->
                    SyncRunCard(run = run, activeRunIds = uiState.activeSyncRunIds)
                }
            }

            // ── Section 4: Upload Queue ──────────────────────────────────────────
            item {
                Text(
                    text = stringResource(R.string.sync_queue_title, uiState.queueTotal),
                    fontSize = 17.sp,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onBackground
                )
            }

            if (uiState.queueTotal == 0) {
                item {
                    EmptyState(
                        icon = Icons.Default.CloudUpload,
                        title = "Nenhum item na fila",
                        message = "Suas fotos e vídeos locais aparecerão aqui quando forem detectados e enviados."
                    )
                }
            } else {
                // Milhares de cartões idênticos não dizem mais que quatro números,
                // e o usuário tinha de rolar por todos eles para chegar a qualquer
                // outra coisa. O resumo responde "como está indo?"; a lista existe
                // para inspecionar casos específicos, então fica fechada.
                item { QueueSummary(counts = uiState.queueCounts) }
                item {
                    SectionToggle(
                        title = stringResource(R.string.sync_queue_section),
                        subtitle = if (uiState.queueTotal > SyncViewModel.QUEUE_WINDOW) {
                            stringResource(
                                R.string.sync_queue_window,
                                SyncViewModel.QUEUE_WINDOW,
                                uiState.queueTotal
                            )
                        } else {
                            stringResource(R.string.sync_queue_window_all, uiState.queueTotal)
                        },
                        expanded = uiState.isQueueExpanded,
                        onToggle = viewModel::toggleQueue
                    )
                }
                if (uiState.isQueueExpanded) {
                    items(uiState.uploadQueue, key = { it.id }) { job ->
                        UploadJobCard(job = job)
                    }
                }
            }

            item {
                Spacer(modifier = Modifier.height(32.dp))
            }
        }
    }

    if (showDeviceSourcePicker) {
        DeviceMediaSourcePicker(
            sources = uiState.availableSources,
            selectedIds = uiState.selectedSourceIds,
            isLoading = uiState.isDiscoveringSources,
            hasLimitedMediaAccess = mediaLibraryAccess == MediaLibraryAccess.LIMITED,
            errorMessage = uiState.sourceDiscoveryError?.let { message ->
                if (message == "MEDIA_PERMISSION_REQUIRED") {
                    stringResource(R.string.sync_media_permission_required)
                } else {
                    message
                }
            },
            onToggleSource = { sourceId, enabled -> viewModel.toggleSource(sourceId, enabled, context) },
            onClearSelection = { viewModel.clearSelectedSources(context) },
            onRefresh = viewModel::discoverSources,
            onRequestMediaAccess = { requestMediaPermission(MediaPermissionFollowUp.DiscoverFolders) },
            onDismiss = { showDeviceSourcePicker = false }
        )
    }
}

/** Header that opens and closes a section, keeping its summary always visible. */
@Composable
private fun SectionToggle(
    title: String,
    subtitle: String,
    expanded: Boolean,
    onToggle: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .clickable(onClick = onToggle)
            .padding(vertical = 10.dp, horizontal = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            Text(
                subtitle,
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Icon(
            imageVector = if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
            contentDescription = stringResource(
                if (expanded) R.string.section_collapse else R.string.section_expand
            ),
            tint = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

/**
 * Total, saved and remaining, as the first thing under the status: what a
 * backup screen is asked ("is it done, how much is left"), apart from what
 * the device is doing right now.
 */
@Composable
private fun BackupCounters(counts: Map<UploadJobState, Int>) {
    val summary = remember(counts) { UploadQueueSummary.from(counts) }
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        listOf(
            stringResource(R.string.backup_counter_total) to summary.total,
            stringResource(R.string.backup_counter_saved) to summary.saved,
            stringResource(R.string.backup_counter_remaining) to summary.remaining,
        ).forEach { (label, value) ->
            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.weight(1f)) {
                Text("$value", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Text(label, fontSize = 12.sp, color = IrisTextSoft)
            }
        }
    }
    if (summary.failed > 0) {
        Text(
            stringResource(R.string.backup_counter_failed, summary.failed),
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.error,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

/** Four numbers that answer "how is the queue going" without listing it. */
@Composable
private fun QueueSummary(counts: Map<UploadJobState, Int>) {
    val summary = remember(counts) { UploadQueueSummary.from(counts) }
    val entries = listOf(
        stringResource(R.string.queue_state_pending) to summary.queued,
        stringResource(R.string.queue_state_sending) to summary.uploading,
        stringResource(R.string.queue_state_processing) to summary.processing,
        stringResource(R.string.queue_state_uploaded) to summary.uploaded,
        stringResource(R.string.queue_state_already_on_server) to summary.alreadyOnServer,
        stringResource(R.string.queue_state_failed) to summary.failed
    ).filter { it.second > 0 }

    Column(modifier = Modifier.fillMaxWidth()) {
        entries.forEach { (label, count) ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 3.dp),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(label, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("$count", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            }
        }
    }
}

@Composable
private fun PreferenceSwitch(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(text = title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(
                text = subtitle,
                fontSize = 12.sp,
                color = IrisTextSoft,
                // A device folder path can be five lines long. Truncating keeps
                // the head, which is the part that says where it came from.
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
        }
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = IrisAccentLime,
                checkedTrackColor = IrisDarkSurfaceBright
            )
        )
    }
}

@Composable
private fun UploadJobCard(job: LocalUploadJob) {
    Card(
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = job.filename,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 14.sp,
                    maxLines = 1,
                    modifier = Modifier.weight(1f)
                )
                Spacer(modifier = Modifier.width(8.dp))
                JobStateBadge(state = job.state)
            }

            Spacer(modifier = Modifier.height(6.dp))

            val sizeMb = job.byteSize / (1024.0 * 1024.0)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = String.format("%.2f MB", sizeMb),
                    fontSize = 12.sp,
                    color = IrisTextSoft
                )
                if (job.state == UploadJobState.UPLOADING && job.byteSize > 0) {
                    val progressPercent = (job.nextByteOffset.toFloat() / job.byteSize.toFloat() * 100).toInt()
                    Text(
                        text = "$progressPercent%",
                        fontSize = 12.sp,
                        color = IrisAccentLime,
                        fontWeight = FontWeight.Bold
                    )
                }
            }

            if (job.state == UploadJobState.UPLOADING) {
                Spacer(modifier = Modifier.height(6.dp))
                val progress = if (job.byteSize > 0) job.nextByteOffset.toFloat() / job.byteSize.toFloat() else 0f
                LinearProgressIndicator(
                    progress = { progress },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(4.dp),
                    color = IrisAccentLime,
                    trackColor = IrisDarkSurfaceBright
                )
            }

            if (job.state == UploadJobState.FAILED && !job.errorMessage.isNullOrBlank()) {
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = job.errorMessage,
                    fontSize = 11.sp,
                    color = IrisDanger
                )
            }
        }
    }
}

@Composable
private fun JobStateBadge(state: UploadJobState) {
    val (label, bg, fg) = when (state) {
        UploadJobState.QUEUED -> Triple("Pendente", IrisDarkSurfaceBright, IrisTextSoft)
        UploadJobState.UPLOADING -> Triple("Enviando…", IrisAccentLime, IrisAccentInk)
        UploadJobState.PENDING_PROCESSING -> Triple("Processando (Backup OK)", IrisViolet, Color.White)
        UploadJobState.PROCESSING -> Triple("Processando…", IrisViolet, Color.White)
        UploadJobState.READY -> Triple("Pronto", Color(0xFF2E7D32), Color.White)
        UploadJobState.DUPLICATE -> Triple("Duplicado", Color(0xFF1565C0), Color.White)
        UploadJobState.FAILED, UploadJobState.FAILED_PROCESSING -> Triple("Falhou", IrisDanger, Color.White)
    }

    Box(
        modifier = Modifier
            .background(bg, RoundedCornerShape(6.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp)
    ) {
        Text(
            text = label,
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            color = fg
        )
    }
}

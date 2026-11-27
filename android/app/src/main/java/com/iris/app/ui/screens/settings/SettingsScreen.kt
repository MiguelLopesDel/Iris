package com.iris.app.ui.screens.settings

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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.material3.OutlinedButton
import com.iris.app.R
import com.journeyapps.barcodescanner.ScanOptions
import com.journeyapps.barcodescanner.ScanContract
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.ui.theme.IrisOnAccent
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisBackground
import com.iris.app.ui.theme.IrisSurface
import com.iris.app.ui.theme.IrisSurfaceBright
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.performance.PerformanceMonitor

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    performanceMonitor: PerformanceMonitor,
    onBack: () -> Unit
) {
val uiState by viewModel.uiState.collectAsStateWithLifecycle()
val performanceReport by performanceMonitor.report.collectAsStateWithLifecycle()
    val context = LocalContext.current
    // A CA certificate chosen by the user, read in full: these files are a few kilobytes.
    val authorityPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            val bytes = runCatching {
                context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            }.getOrNull()
            if (bytes != null && bytes.size <= 1_048_576) viewModel.importAuthority(bytes)
        }
    }
    uiState.connectionProblem?.let { problem ->
        ConnectionProblemDialog(
            problem,
            ConnectionProblemActions(
                allowCleartext = viewModel::allowCleartext,
                trustDeviceCertificates = viewModel::trustDeviceCertificates,
                trustPresentedCertificate = viewModel::trustPresentedCertificate,
                importAuthority = {
                    authorityPicker.launch(
                        arrayOf("application/x-x509-ca-cert", "application/pkix-cert", "application/x-pem-file", "*/*")
                    )
                },
                dismiss = viewModel::dismissConnectionProblem,
            ),
        )
    }
    val scanPrompt = stringResource(R.string.settings_scan_prompt)
    var choosingPairing by remember { mutableStateOf(false) }
    val qrScanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        result.contents?.let(viewModel::readPairingCode)
    }
    if (choosingPairing) {
        PairingEntryDialog(
            onScan = {
                choosingPairing = false
                qrScanner.launch(
                    ScanOptions()
                        .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                        .setPrompt(scanPrompt)
                        .setBeepEnabled(false)
                        .setOrientationLocked(false)
                )
            },
            onPaste = { text ->
                choosingPairing = false
                viewModel.readPairingCode(text)
            },
            onDismiss = { choosingPairing = false },
        )
    }
    uiState.pendingPairing?.let { code ->
        PairingConfirmDialog(code, onConfirm = viewModel::confirmPairing, onCancel = viewModel::cancelPairing)
    }
    uiState.pairingMessage?.let { message ->
        AlertDialog(
            onDismissRequest = viewModel::dismissPairingMessage,
            title = { Text(stringResource(R.string.settings_pairing_title)) },
            text = { Text(message) },
            confirmButton = { TextButton(onClick = viewModel::dismissPairingMessage) { Text(stringResource(R.string.common_ok)) } },
        )
    }
    uiState.securityMessage?.let { message ->
        AlertDialog(
            onDismissRequest = viewModel::dismissSecurityMessage,
            title = { Text(stringResource(R.string.settings_certificate_title)) },
            text = { Text(message) },
            confirmButton = { TextButton(onClick = viewModel::dismissSecurityMessage) { Text(stringResource(R.string.common_ok)) } },
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.settings_title),
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = stringResource(R.string.common_back)
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisBackground)
            )
        },
        containerColor = IrisBackground
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(16.dp)
        ) {
            val themeMode by viewModel.themeMode.collectAsStateWithLifecycle()
            val themeColors by viewModel.themeColors.collectAsStateWithLifecycle()
            AppearanceSettings(
                mode = themeMode,
                colors = themeColors,
                onMode = viewModel::setThemeMode,
                onColors = viewModel::setThemeColors,
            )
            Spacer(modifier = Modifier.height(16.dp))
            HorizontalDivider(color = IrisSurfaceBright)
            Spacer(modifier = Modifier.height(16.dp))

            Text(
                text = stringResource(R.string.settings_connection_title),
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                color = IrisAccent
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = stringResource(R.string.settings_connection_help),
                fontSize = 13.sp,
                color = IrisTextSoft
            )

            Spacer(modifier = Modifier.height(16.dp))

            OutlinedTextField(
                value = uiState.serverUrl,
                onValueChange = { viewModel.onServerUrlChange(it) },
                label = { Text(stringResource(R.string.settings_server_url)) },
                placeholder = { Text("http://192.168.1.100:8000/") },
                leadingIcon = {
                    Icon(imageVector = Icons.Default.Dns, contentDescription = null)
                },
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedContainerColor = IrisSurface,
                    unfocusedContainerColor = IrisSurface,
                    focusedBorderColor = IrisAccent,
                    unfocusedBorderColor = IrisSurfaceBright
                ),
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(modifier = Modifier.height(8.dp))

            // Presets
            Text(
                text = stringResource(R.string.settings_shortcuts),
                fontSize = 12.sp,
                color = IrisTextMuted
            )
            Spacer(modifier = Modifier.height(4.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = uiState.serverUrl.contains("10.0.2.2"),
                    onClick = { viewModel.onServerUrlChange("http://10.0.2.2:8000/") },
                    label = { Text(stringResource(R.string.settings_shortcut_emulator)) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccent,
                        selectedLabelColor = IrisOnAccent
                    )
                )
                FilterChip(
                    selected = uiState.serverUrl.contains("localhost"),
                    onClick = { viewModel.onServerUrlChange("http://localhost:8000/") },
                    label = { Text(stringResource(R.string.settings_shortcut_localhost)) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccent,
                        selectedLabelColor = IrisOnAccent
                    )
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            Button(
                onClick = { viewModel.saveAndTestConnection() },
                enabled = !uiState.isTestingConnection && uiState.serverUrl.isNotBlank(),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = IrisAccent,
                    contentColor = IrisOnAccent
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
            ) {
                if (uiState.isTestingConnection) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = IrisOnAccent,
                        strokeWidth = 2.dp
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(stringResource(R.string.settings_testing), fontWeight = FontWeight.Bold)
                } else {
                    Text(stringResource(R.string.settings_save_test), fontWeight = FontWeight.Bold)
                }
            }

            Spacer(modifier = Modifier.height(8.dp))
            OutlinedButton(
                onClick = { choosingPairing = true },
                enabled = !uiState.isPairing,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth().height(48.dp),
            ) {
                if (uiState.isPairing) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(stringResource(R.string.settings_pairing_progress))
                } else {
                    Text(stringResource(R.string.pairing_entry_title))
                }
            }

            // Connection test feedback (/healthz probe)
            if (uiState.connectionTestResult != null) {
                Spacer(modifier = Modifier.height(16.dp))
                val isOnline = uiState.isServerOnline == true
                Card(
                    shape = RoundedCornerShape(12.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisSurface),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier.padding(14.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(
                            imageVector = if (isOnline) Icons.Default.CheckCircle else Icons.Default.Error,
                            contentDescription = null,
                            tint = if (isOnline) IrisAccent else IrisDanger,
                            modifier = Modifier.size(24.dp)
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Column {
                            Text(
                                text = stringResource(if (isOnline) R.string.settings_server_connected else R.string.settings_connection_failed),
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Bold,
                                color = if (isOnline) IrisAccent else IrisDanger
                            )
                            Text(
                                text = uiState.connectionTestResult ?: "",
                                fontSize = 12.sp,
                                color = IrisTextSoft
                            )
                        }
                    }
                }
            }

            uiState.security?.let { summary ->
                Spacer(modifier = Modifier.height(16.dp))
                ConnectionSecurityCard(summary, onReset = viewModel::resetSecurity)
            }

            // Account & Library Access Status Card
            Spacer(modifier = Modifier.height(16.dp))
            Card(
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = IrisSurface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = stringResource(R.string.settings_library_access),
                        fontSize = 15.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onBackground
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    if (uiState.isDeviceLoggedIn) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .background(IrisAccent, CircleShape)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = stringResource(R.string.settings_device_signed_in, uiState.loggedInUsername),
                                fontSize = 13.sp,
                                color = IrisAccent,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                        if (uiState.deviceId.isNotBlank()) {
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = stringResource(R.string.settings_device_id, uiState.deviceId.take(16)),
                                fontSize = 11.sp,
                                color = IrisTextMuted
                            )
                        }
                    } else {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .background(Color(0xFFFFB300), CircleShape)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = stringResource(R.string.settings_device_signed_out),
                                fontSize = 13.sp,
                                color = Color(0xFFFFB300),
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = stringResource(R.string.settings_device_signed_out_hint),
                            fontSize = 12.sp,
                            color = IrisTextSoft
                        )
                    }
                }
            }

            // Server diagnostics card (if available)
            if (uiState.serverInfo != null) {
                val info = uiState.serverInfo!!
                Spacer(modifier = Modifier.height(24.dp))
                HorizontalDivider(color = IrisSurfaceBright)
                Spacer(modifier = Modifier.height(16.dp))

                Text(
                    text = stringResource(R.string.settings_library_info),
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onBackground
                )
                Spacer(modifier = Modifier.height(12.dp))

                Card(
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisSurface),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        InfoRow(label = stringResource(R.string.settings_info_total_media), value = "${info.records}")
                        InfoRow(label = stringResource(R.string.settings_info_ai_device), value = info.device.ifBlank { "CUDA / CPU" })
                        InfoRow(label = stringResource(R.string.settings_info_embeddings_model), value = info.model.ifBlank { "CLIP ViT-L-14" })
                        if (info.florenceModel.isNotBlank()) {
                            InfoRow(label = stringResource(R.string.settings_info_vlm_model), value = info.florenceModel)
                        }
                        InfoRow(label = stringResource(R.string.settings_info_database), value = info.db.ifBlank { "iris_v1.db" })
                        InfoRow(
                            label = stringResource(R.string.settings_info_faiss_index),
                            value = stringResource(if (info.faissIndexExists) R.string.settings_info_faiss_active else R.string.settings_info_faiss_inactive)
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(24.dp))
            HorizontalDivider(color = IrisSurfaceBright)
            Spacer(modifier = Modifier.height(16.dp))
            Text(
                text = stringResource(R.string.settings_perf_title),
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = stringResource(R.string.settings_perf_help),
                fontSize = 12.sp,
                color = IrisTextSoft
            )
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = {
                    if (performanceReport.enabled) performanceMonitor.stop() else performanceMonitor.start()
                },
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (performanceReport.enabled) IrisSurfaceBright else IrisAccent,
                    contentColor = if (performanceReport.enabled) IrisTextSoft else IrisOnAccent
                ),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(stringResource(if (performanceReport.enabled) R.string.settings_perf_stop else R.string.settings_perf_start))
            }
            if (performanceReport.metrics.isNotEmpty() || performanceReport.upload != null) {
                Spacer(modifier = Modifier.height(12.dp))
                Card(
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisSurface),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(stringResource(R.string.settings_perf_median), fontSize = 12.sp, color = IrisTextMuted)
                        performanceReport.upload?.let { upload ->
                            InfoRow(
                                label = "sync.upload_throughput",
                                value = stringResource(
                                    R.string.settings_perf_upload_throughput,
                                    upload.activeMibPerSecond * 1.048576,
                                    upload.mibPerSecond,
                                    upload.bytes / 1_048_576.0,
                                    upload.elapsedMillis / 1_000.0,
                                    upload.runs
                                )
                            )
                            InfoRow(
                                label = "sync.confirmed_items",
                                value = stringResource(
                                    R.string.settings_perf_confirmed_items,
                                    upload.itemsPerSecond,
                                    upload.confirmedItems,
                                    upload.runs
                                )
                            )
                        }
                        performanceReport.metrics.sortedBy { it.name }.forEach { metric ->
                            InfoRow(
                                label = metric.name,
                                value = "%d / %d / %d ms (%d)".format(
                                    metric.medianMs.toInt(),
                                    metric.p90Ms.toInt(),
                                    metric.maxMs.toInt(),
                                    metric.count
                                )
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(text = label, fontSize = 13.sp, color = IrisTextSoft)
        Text(text = value, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = IrisAccent)
    }
}

package com.iris.app.ui.screens.sync

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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.CloudDone
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.ContentCopy
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
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.data.model.LocalUploadJob
import com.iris.app.data.model.UploadJobState
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisDarkBg
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisDarkSurfaceBright
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.ui.theme.IrisViolet

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SyncScreen(
    viewModel: SyncViewModel
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current

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
                                enabled = !uiState.isLoggingIn && uiState.loginUsernameInput.isNotBlank() && uiState.loginPasswordInput.isNotBlank(),
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
                                Text(
                                    text = if (uiState.isSyncing) "Enviando mídias em segundo plano…" else "Pronto para sincronizar",
                                    fontSize = 12.sp,
                                    color = if (uiState.isSyncing) IrisAccentLime else IrisTextSoft
                                )
                            }

                            Button(
                                onClick = { viewModel.triggerManualSync(context) },
                                enabled = uiState.isLoggedIn && !uiState.isSyncing,
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
                                Text("Sincronizar", fontWeight = FontWeight.Bold, fontSize = 13.sp)
                            }
                        }

                        if (uiState.isSyncing && uiState.currentProgress > 0f) {
                            Spacer(modifier = Modifier.height(12.dp))
                            LinearProgressIndicator(
                                progress = { uiState.currentProgress },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(6.dp),
                                color = IrisAccentLime,
                                trackColor = IrisDarkSurfaceBright
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
                            onCheckedChange = { viewModel.setAutoBackupEnabled(it) }
                        )

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

            // ── Section 3: Upload Queue & History ────────────────────────────────
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Fila de Envio (${uiState.uploadQueue.size})",
                        fontSize = 17.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onBackground
                    )
                }
            }

            if (uiState.uploadQueue.isEmpty()) {
                item {
                    EmptyState(
                        icon = Icons.Default.CloudUpload,
                        title = "Nenhum item na fila",
                        message = "Suas fotos e vídeos locais aparecerão aqui quando forem detectados e enviados."
                    )
                }
            } else {
                items(uiState.uploadQueue, key = { it.id }) { job ->
                    UploadJobCard(job = job)
                }
            }

            item {
                Spacer(modifier = Modifier.height(32.dp))
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
            Text(text = subtitle, fontSize = 12.sp, color = IrisTextSoft)
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

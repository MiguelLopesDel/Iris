package com.iris.app.ui.screens.settings

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
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisDarkBg
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisDarkSurfaceBright
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
    val uiState by viewModel.uiState.collectAsState()
    val performanceReport by performanceMonitor.report.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "Configurações do Servidor",
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Voltar"
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisDarkBg)
            )
        },
        containerColor = IrisDarkBg
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(16.dp)
        ) {
            Text(
                text = "Conexão com o Servidor Iris",
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                color = IrisAccentLime
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "Insira o endereço IP ou domínio onde a API do Iris está rodando (FastAPI).",
                fontSize = 13.sp,
                color = IrisTextSoft
            )

            Spacer(modifier = Modifier.height(16.dp))

            OutlinedTextField(
                value = uiState.serverUrl,
                onValueChange = { viewModel.onServerUrlChange(it) },
                label = { Text("URL do Servidor") },
                placeholder = { Text("http://192.168.1.100:8000/") },
                leadingIcon = {
                    Icon(imageVector = Icons.Default.Dns, contentDescription = null)
                },
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedContainerColor = IrisDarkSurface,
                    unfocusedContainerColor = IrisDarkSurface,
                    focusedBorderColor = IrisAccentLime,
                    unfocusedBorderColor = IrisDarkSurfaceBright
                ),
                modifier = Modifier.fillMaxWidth()
            )

            Spacer(modifier = Modifier.height(8.dp))

            // Presets
            Text(
                text = "Atalhos rápidos:",
                fontSize = 12.sp,
                color = IrisTextMuted
            )
            Spacer(modifier = Modifier.height(4.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = uiState.serverUrl.contains("10.0.2.2"),
                    onClick = { viewModel.onServerUrlChange("http://10.0.2.2:8000/") },
                    label = { Text("Emulador (10.0.2.2)") },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccentLime,
                        selectedLabelColor = IrisAccentInk
                    )
                )
                FilterChip(
                    selected = uiState.serverUrl.contains("localhost"),
                    onClick = { viewModel.onServerUrlChange("http://localhost:8000/") },
                    label = { Text("Localhost") },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccentLime,
                        selectedLabelColor = IrisAccentInk
                    )
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            Button(
                onClick = { viewModel.saveAndTestConnection() },
                enabled = !uiState.isTestingConnection && uiState.serverUrl.isNotBlank(),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = IrisAccentLime,
                    contentColor = IrisAccentInk
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
            ) {
                if (uiState.isTestingConnection) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = IrisAccentInk,
                        strokeWidth = 2.dp
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Testando conexão…", fontWeight = FontWeight.Bold)
                } else {
                    Text("Salvar e Testar Conexão", fontWeight = FontWeight.Bold)
                }
            }

            // Connection test feedback (/healthz probe)
            if (uiState.connectionTestResult != null) {
                Spacer(modifier = Modifier.height(16.dp))
                val isOnline = uiState.isServerOnline == true
                Card(
                    shape = RoundedCornerShape(12.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier.padding(14.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(
                            imageVector = if (isOnline) Icons.Default.CheckCircle else Icons.Default.Error,
                            contentDescription = null,
                            tint = if (isOnline) IrisAccentLime else IrisDanger,
                            modifier = Modifier.size(24.dp)
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Column {
                            Text(
                                text = if (isOnline) "Servidor Iris Conectado" else "Falha de Conexão",
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Bold,
                                color = if (isOnline) IrisAccentLime else IrisDanger
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

            // Account & Library Access Status Card
            Spacer(modifier = Modifier.height(16.dp))
            Card(
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Acesso à Biblioteca Privada",
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
                                    .background(IrisAccentLime, CircleShape)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = "Dispositivo autenticado como ${uiState.loggedInUsername}",
                                fontSize = 13.sp,
                                color = IrisAccentLime,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                        if (uiState.deviceId.isNotBlank()) {
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = "ID do Dispositivo: ${uiState.deviceId.take(16)}…",
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
                                text = "Dispositivo não autenticado",
                                fontSize = 13.sp,
                                color = Color(0xFFFFB300),
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "O servidor está acessível, mas para visualizar e sincronizar sua biblioteca privada de memes, realize o login do dispositivo na aba 'Backup'.",
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
                HorizontalDivider(color = IrisDarkSurfaceBright)
                Spacer(modifier = Modifier.height(16.dp))

                Text(
                    text = "Informações da Biblioteca",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onBackground
                )
                Spacer(modifier = Modifier.height(12.dp))

                Card(
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        InfoRow(label = "Total de Mídias Indexadas", value = "${info.records}")
                        InfoRow(label = "Dispositivo de IA", value = info.device.ifBlank { "CUDA / CPU" })
                        InfoRow(label = "Modelo de Embeddings", value = info.model.ifBlank { "CLIP ViT-L-14" })
                        if (info.florenceModel.isNotBlank()) {
                            InfoRow(label = "Modelo VLM", value = info.florenceModel)
                        }
                        InfoRow(label = "Banco de Dados", value = info.db.ifBlank { "iris_v1.db" })
                        InfoRow(
                            label = "Índice FAISS",
                            value = if (info.faissIndexExists) "Ativo e carregado" else "Não inicializado"
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(24.dp))
            HorizontalDivider(color = IrisDarkSurfaceBright)
            Spacer(modifier = Modifier.height(16.dp))
            Text(
                text = "Diagnóstico de desempenho",
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onBackground
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "Mede somente tempos agregados neste aparelho. Não registra fotos, URLs, contas ou senhas.",
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
                    containerColor = if (performanceReport.enabled) IrisDarkSurfaceBright else IrisAccentLime,
                    contentColor = if (performanceReport.enabled) IrisTextSoft else IrisAccentInk
                ),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(if (performanceReport.enabled) "Parar diagnóstico" else "Iniciar diagnóstico")
            }
            if (performanceReport.metrics.isNotEmpty()) {
                Spacer(modifier = Modifier.height(12.dp))
                Card(
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text("Mediana / P90 / máximo", fontSize = 12.sp, color = IrisTextMuted)
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
        Text(text = value, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = IrisAccentLime)
    }
}

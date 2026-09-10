package com.iris.app.ui.screens.persons

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.Face
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import coil.compose.SubcomposeAsyncImage
import coil.request.ImageRequest
import com.iris.app.IrisApplication
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.MediaCard
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDarkBg
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisDarkSurfaceBright
import com.iris.app.ui.theme.IrisTextSoft

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PersonsScreen(
    viewModel: PersonsViewModel,
    onPersonClick: (Int, String) -> Unit
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val apiClient = (context.applicationContext as IrisApplication).apiClient

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "Pessoas",
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold
                    )
                },
                actions = {
                    IconButton(onClick = { viewModel.loadPersons(isRefresh = true) }) {
                        Icon(imageVector = Icons.Default.Refresh, contentDescription = "Atualizar")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisDarkBg)
            )
        },
        containerColor = IrisDarkBg
    ) { paddingValues ->
        PullToRefreshBox(
            isRefreshing = uiState.isRefreshing,
            onRefresh = { viewModel.loadPersons(isRefresh = true) },
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            when {
                uiState.isLoading && uiState.persons.isEmpty() -> {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        CircularProgressIndicator(color = IrisAccentLime)
                    }
                }

                uiState.error != null && uiState.persons.isEmpty() -> {
                    EmptyState(
                        title = "Erro ao carregar pessoas",
                        message = uiState.error ?: "",
                        actionLabel = "Tentar novamente",
                        onAction = { viewModel.loadPersons() }
                    )
                }

                uiState.persons.isEmpty() -> {
                    EmptyState(
                        icon = Icons.Outlined.Face,
                        title = "Nenhuma pessoa encontrada",
                        message = "O reconhecimento facial não detectou rostos agrupados no servidor.",
                        actionLabel = "Atualizar",
                        onAction = { viewModel.loadPersons(isRefresh = true) }
                    )
                }

                else -> {
                    LazyVerticalGrid(
                        columns = GridCells.Adaptive(130.dp),
                        contentPadding = PaddingValues(12.dp),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                        modifier = Modifier.fillMaxSize()
                    ) {
                        items(
                            items = uiState.persons,
                            key = { it.id }
                        ) { person ->
                            val faceThumbUrl = person.coverFaceId?.let {
                                apiClient.resolveFaceThumbnailUrl(it)
                            }

                            Card(
                                shape = RoundedCornerShape(16.dp),
                                colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clip(RoundedCornerShape(16.dp))
                                    .clickable { onPersonClick(person.id, person.name) }
                            ) {
                                Column(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(12.dp),
                                    horizontalAlignment = Alignment.CenterHorizontally
                                ) {
                                    Box(
                                        modifier = Modifier
                                            .size(72.dp)
                                            .clip(CircleShape)
                                            .background(IrisDarkSurfaceBright),
                                        contentAlignment = Alignment.Center
                                    ) {
                                        if (faceThumbUrl != null) {
                                            SubcomposeAsyncImage(
                                                model = ImageRequest.Builder(context)
                                                    .data(faceThumbUrl)
                                                    .crossfade(false)
                                                    .build(),
                                                contentDescription = person.name,
                                                contentScale = ContentScale.Crop,
                                                modifier = Modifier.fillMaxSize(),
                                                loading = {
                                                    CircularProgressIndicator(
                                                        modifier = Modifier.size(20.dp),
                                                        strokeWidth = 2.dp,
                                                        color = IrisAccentLime
                                                    )
                                                },
                                                error = {
                                                    Icon(
                                                        imageVector = Icons.Default.Person,
                                                        contentDescription = null,
                                                        tint = IrisTextSoft,
                                                        modifier = Modifier.size(36.dp)
                                                    )
                                                }
                                            )
                                        } else {
                                            Icon(
                                                imageVector = Icons.Default.Person,
                                                contentDescription = null,
                                                tint = IrisTextSoft,
                                                modifier = Modifier.size(36.dp)
                                            )
                                        }
                                    }

                                    Spacer(modifier = Modifier.height(8.dp))
                                    Text(
                                        text = person.name.ifBlank { "Pessoa #${person.id}" },
                                        fontWeight = FontWeight.SemiBold,
                                        fontSize = 14.sp,
                                        maxLines = 1,
                                        textAlign = TextAlign.Center
                                    )
                                    Spacer(modifier = Modifier.height(2.dp))
                                    Text(
                                        text = "${person.mediaCount} mídias",
                                        fontSize = 11.sp,
                                        color = IrisTextSoft
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PersonMediaScreen(
    viewModel: PersonMediaViewModel,
    onBack: () -> Unit,
    onMediaClick: (Int) -> Unit
) {
    val uiState by viewModel.uiState.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = uiState.personName.ifBlank { "Pessoa #${uiState.personId}" },
                            fontSize = 18.sp,
                            fontWeight = FontWeight.Bold
                        )
                        if (uiState.total > 0) {
                            Text(
                                text = "${uiState.total} mídias",
                                fontSize = 12.sp,
                                color = IrisTextSoft
                            )
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Voltar"
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { viewModel.loadMedia() }) {
                        Icon(imageVector = Icons.Default.Refresh, contentDescription = "Atualizar")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisDarkBg)
            )
        },
        containerColor = IrisDarkBg
    ) { paddingValues ->
        when {
            uiState.isLoading -> {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(paddingValues),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = IrisAccentLime)
                }
            }

            uiState.error != null -> {
                EmptyState(
                    title = "Erro ao carregar mídias",
                    message = uiState.error ?: "",
                    actionLabel = "Tentar novamente",
                    onAction = { viewModel.loadMedia() },
                    modifier = Modifier.padding(paddingValues)
                )
            }

            uiState.media.isEmpty() -> {
                EmptyState(
                    title = "Nenhuma mídia vinculada",
                    message = "Esta pessoa não possui mídias cadastradas.",
                    modifier = Modifier.padding(paddingValues)
                )
            }

            else -> {
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(110.dp),
                    contentPadding = PaddingValues(8.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(paddingValues)
                ) {
                    items(
                        items = uiState.media,
                        key = { it.index }
                    ) { record ->
                        MediaCard(
                            record = record,
                            onClick = { onMediaClick(record.index) }
                        )
                    }
                }
            }
        }
    }
}

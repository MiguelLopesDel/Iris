package com.iris.app.ui.screens.collections

import com.iris.app.ui.text.countOf
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.PhoneAndroid
import androidx.compose.material.icons.outlined.StarBorder
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.FolderSpecial
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.withFrameNanos
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.MediaCard
import com.iris.app.ui.theme.IrisOnAccent
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisBackground
import com.iris.app.ui.theme.IrisSurface
import com.iris.app.ui.theme.IrisSurfaceBright
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.ui.theme.IrisViolet

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CollectionsScreen(
    viewModel: CollectionsViewModel,
    onCollectionClick: (Int, String) -> Unit,
    onPeopleClick: () -> Unit,
    onGroupsClick: () -> Unit = {},
    onFavoritesClick: () -> Unit = {},
    onTrashClick: () -> Unit = {},
    onDocumentsClick: () -> Unit = {},
    onDeviceFoldersClick: () -> Unit = {}
) {
val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "Álbuns",
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold
                    )
                },
                actions = {
                    IconButton(onClick = { viewModel.loadData(isRefresh = true) }) {
                        Icon(imageVector = Icons.Default.Refresh, contentDescription = "Atualizar")
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
        ) {
            // Scrollable: four labels do not fit a narrow phone side by side.
            ScrollableTabRow(
                selectedTabIndex = uiState.selectedTab,
                edgePadding = 0.dp,
                containerColor = IrisBackground,
                contentColor = IrisAccent,
                indicator = { tabPositions ->
                    TabRowDefaults.SecondaryIndicator(
                        Modifier.tabIndicatorOffset(tabPositions[uiState.selectedTab]),
                        color = IrisAccent
                    )
                }
            ) {
                Tab(
                    selected = uiState.selectedTab == 0,
                    onClick = { viewModel.setTab(0) },
                    text = { Text("Álbuns (${uiState.collections.size})", fontWeight = FontWeight.SemiBold) }
                )
                Tab(
                    selected = false,
                    onClick = onPeopleClick,
                    text = { Text("Pessoas", fontWeight = FontWeight.SemiBold) }
                )
                Tab(
                    selected = false,
                    onClick = onGroupsClick,
                    text = { Text("Compartilhados", fontWeight = FontWeight.SemiBold) }
                )
            }

            // Pinned places, as a gallery app keeps them: always one tap away.
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                AlbumShortcut(Icons.Outlined.StarBorder, "Favoritos", onFavoritesClick, Modifier.weight(1f))
                AlbumShortcut(Icons.Outlined.Description, "Documentos", onDocumentsClick, Modifier.weight(1f))
                AlbumShortcut(Icons.Outlined.PhoneAndroid, "No celular", onDeviceFoldersClick, Modifier.weight(1f))
                AlbumShortcut(Icons.Outlined.Delete, "Lixeira", onTrashClick, Modifier.weight(1f))
            }

            PullToRefreshBox(
                isRefreshing = uiState.isRefreshing,
                onRefresh = { viewModel.loadData(isRefresh = true) },
                modifier = Modifier.fillMaxSize()
            ) {
                when {
                    uiState.isLoading && uiState.collections.isEmpty() && uiState.concepts.isEmpty() -> {
                        Box(
                            modifier = Modifier.fillMaxSize(),
                            contentAlignment = Alignment.Center
                        ) {
                            CircularProgressIndicator(color = IrisAccent)
                        }
                    }

                    uiState.selectedTab == 0 -> {
                        // Coleções tab
                        if (uiState.collections.isEmpty()) {
                            EmptyState(
                                icon = Icons.Outlined.FolderSpecial,
                                title = "Nenhum álbum",
                                message = "Crie um álbum no Iris para reunir fotos e vídeos.",
                                actionLabel = "Atualizar",
                                onAction = { viewModel.loadData(isRefresh = true) }
                            )
                        } else {
                            LazyColumn(
                                contentPadding = PaddingValues(12.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp),
                                modifier = Modifier.fillMaxSize()
                            ) {
                                items(
                                    items = uiState.collections,
                                    key = { it.id }
                                ) { col ->
                                    Card(
                                        shape = RoundedCornerShape(14.dp),
                                        colors = CardDefaults.cardColors(containerColor = IrisSurface),
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .clip(RoundedCornerShape(14.dp))
                                            .clickable { onCollectionClick(col.id, col.name) }
                                    ) {
                                        Row(
                                            modifier = Modifier
                                                .fillMaxWidth()
                                                .padding(16.dp),
                                            verticalAlignment = Alignment.CenterVertically
                                        ) {
                                            Icon(
                                                imageVector = Icons.Default.Folder,
                                                contentDescription = null,
                                                tint = IrisAccent,
                                                modifier = Modifier.size(32.dp)
                                            )
                                            Spacer(modifier = Modifier.width(16.dp))
                                            Column(modifier = Modifier.weight(1f)) {
                                                Text(
                                                    text = col.name,
                                                    fontSize = 16.sp,
                                                    fontWeight = FontWeight.SemiBold
                                                )
                                                Text(
                                                    text = countOf(col.count, "item", "itens"),
                                                    fontSize = 13.sp,
                                                    color = IrisTextSoft
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    else -> {
                        // Conceitos tab
                        if (uiState.concepts.isEmpty()) {
                            EmptyState(
                                icon = Icons.Default.Lightbulb,
                                title = "Nenhum conceito",
                                message = "Nenhum conceito temático configurado no servidor.",
                                actionLabel = "Atualizar",
                                onAction = { viewModel.loadData(isRefresh = true) }
                            )
                        } else {
                            LazyColumn(
                                contentPadding = PaddingValues(12.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp),
                                modifier = Modifier.fillMaxSize()
                            ) {
                                items(
                                    items = uiState.concepts,
                                    key = { it.id }
                                ) { concept ->
                                    Card(
                                        shape = RoundedCornerShape(14.dp),
                                        colors = CardDefaults.cardColors(containerColor = IrisSurface),
                                        modifier = Modifier.fillMaxWidth()
                                    ) {
                                        Column(modifier = Modifier.padding(16.dp)) {
                                            Row(
                                                modifier = Modifier.fillMaxWidth(),
                                                horizontalArrangement = Arrangement.SpaceBetween,
                                                verticalAlignment = Alignment.CenterVertically
                                            ) {
                                                Row(verticalAlignment = Alignment.CenterVertically) {
                                                    Icon(
                                                        imageVector = Icons.Default.Lightbulb,
                                                        contentDescription = null,
                                                        tint = IrisViolet,
                                                        modifier = Modifier.size(20.dp)
                                                    )
                                                    Spacer(modifier = Modifier.width(8.dp))
                                                    Text(
                                                        text = concept.name,
                                                        fontSize = 16.sp,
                                                        fontWeight = FontWeight.Bold
                                                    )
                                                }
                                                Text(
                                                    text = countOf(concept.matchCount, "mídia", "mídias"),
                                                    fontSize = 12.sp,
                                                    color = IrisAccent,
                                                    fontWeight = FontWeight.SemiBold
                                                )
                                            }
                                            if (!concept.description.isNullOrBlank()) {
                                                Spacer(modifier = Modifier.height(6.dp))
                                                Text(
                                                    text = concept.description,
                                                    fontSize = 13.sp,
                                                    color = IrisTextSoft
                                                )
                                            }
                                            Spacer(modifier = Modifier.height(4.dp))
                                            Text(
                                                text = "${concept.referenceCount} referências",
                                                fontSize = 11.sp,
                                                color = IrisTextMuted
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
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CollectionMediaScreen(
    viewModel: CollectionMediaViewModel,
    onBack: () -> Unit,
    onMediaClick: (Int) -> Unit
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val gridState = rememberLazyGridState()
    val shouldLoadMore by remember {
        derivedStateOf {
            val lastVisible = gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
            val total = gridState.layoutInfo.totalItemsCount
            total > 0 && lastVisible >= total - 8
        }
    }

    LaunchedEffect(uiState.members.isNotEmpty()) {
        if (uiState.members.isNotEmpty()) {
            withFrameNanos { viewModel.onFirstContentDrawn() }
        }
    }

    LaunchedEffect(shouldLoadMore) {
        if (shouldLoadMore) viewModel.loadNextPage()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = uiState.collectionName,
                            fontSize = 18.sp,
                            fontWeight = FontWeight.Bold
                        )
                        Text(
                            text = countOf(uiState.totalRecords, "mídia", "mídias"),
                            fontSize = 12.sp,
                            color = IrisTextSoft
                        )
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
                    IconButton(onClick = { viewModel.loadMembers() }) {
                        Icon(imageVector = Icons.Default.Refresh, contentDescription = "Atualizar")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisBackground)
            )
        },
        containerColor = IrisBackground
    ) { paddingValues ->
        when {
            uiState.isLoading -> {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(paddingValues),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = IrisAccent)
                }
            }

            uiState.showsErrorScreen -> {
                EmptyState(
                    title = "Erro ao carregar coleção",
                    message = uiState.error ?: "",
                    actionLabel = "Tentar novamente",
                    onAction = { viewModel.loadMembers() },
                    modifier = Modifier.padding(paddingValues)
                )
            }

            uiState.members.isEmpty() -> {
                EmptyState(
                    title = "Coleção vazia",
                    message = "Nenhuma mídia adicionada a esta coleção ainda.",
                    modifier = Modifier.padding(paddingValues)
                )
            }

            else -> {
                LazyVerticalGrid(
                    state = gridState,
                    columns = GridCells.Adaptive(110.dp),
                    contentPadding = PaddingValues(8.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(paddingValues)
                ) {
                    items(
                        items = uiState.members,
                        key = { it.index }
                    ) { record ->
                        MediaCard(
                            record = record,
                            performanceMonitor = viewModel.performanceMonitor,
                            onClick = { onMediaClick(record.index) }
                        )
                    }
                    if (uiState.isLoadingMore) {
                        item(key = "loading-more") {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .aspectRatio(1f),
                                contentAlignment = Alignment.Center
                            ) {
                                CircularProgressIndicator(color = IrisAccent)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AlbumShortcut(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = IrisSurface),
        modifier = modifier
            .clip(RoundedCornerShape(14.dp))
            .clickable(onClick = onClick)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 14.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Icon(icon, contentDescription = null, tint = IrisAccent)
            Spacer(modifier = Modifier.height(6.dp))
            Text(label, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

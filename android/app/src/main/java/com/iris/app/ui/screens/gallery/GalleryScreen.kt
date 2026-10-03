package com.iris.app.ui.screens.gallery

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.outlined.PhotoLibrary
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.CloudConnectionState
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.components.CloudSyncNotice
import com.iris.app.ui.components.MediaCard
import com.iris.app.ui.components.ServerStatusBadge
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDarkBg
import com.iris.app.ui.theme.IrisDarkSurface
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GalleryScreen(
    viewModel: GalleryViewModel,
    onMediaClick: (Int) -> Unit,
    onDeviceMediaClick: (String) -> Unit = {},
    onSettingsClick: () -> Unit,
    onLoginClick: () -> Unit = {}
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val gridState = rememberLazyGridState()
    val mediaPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        viewModel.refreshDeviceMedia()
    }
    val requestedMediaPermissions = remember {
        com.iris.app.ui.screens.sync.mediaPermissionsForSdk(Build.VERSION.SDK_INT)
    }
    // Installs from before ACCESS_MEDIA_LOCATION was requested already hold the
    // media permission; ask for the location one alone so new uploads keep GPS.
    val galleryContext = androidx.compose.ui.platform.LocalContext.current
    val mediaLocationLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { }
    LaunchedEffect(Unit) {
        val mediaGranted = com.iris.app.ui.screens.sync.currentMediaLibraryAccess(galleryContext) !=
            com.iris.app.ui.screens.sync.MediaLibraryAccess.DENIED
        if (Build.VERSION.SDK_INT >= 29 && mediaGranted &&
            !com.iris.app.data.sync.MediaLocationAccess.granted(galleryContext)
        ) {
            mediaLocationLauncher.launch(Manifest.permission.ACCESS_MEDIA_LOCATION)
        }
    }
    // The real cost of a denser grid was the server generating thumbnails
    // synchronously on the FastAPI event loop on a cache miss (fixed
    // server-side: server.py now runs that in a threadpool). 3 is a normal
    // Google-Photos-ish default; pinch still goes denser or coarser.
    var columnCount by rememberSaveable { mutableIntStateOf(3) }

    // Infinite scroll trigger when reaching near the end
    val shouldLoadMore by remember {
        derivedStateOf {
            val totalItems = gridState.layoutInfo.totalItemsCount
            val lastVisibleIndex = gridState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
            // Start loading around two visual rows before the end. The old
            // three-item threshold exposed server latency on every scroll.
            totalItems > 0 && lastVisibleIndex >= totalItems - 12
        }
    }

    // Keyed on the loaded page as well as the trigger. A fling that overshoots
    // the whole loaded range leaves shouldLoadMore stuck at true: the value
    // never changes again, so an effect keyed only on it never re-runs and
    // pagination stops for good until the user scrolls back up. Re-keying on
    // the page means each landed page re-evaluates whether to fetch the next.
    LaunchedEffect(shouldLoadMore, uiState.page, uiState.error) {
        if (shouldLoadMore) {
            viewModel.loadNextPage()
        }
    }

    // A filter replaces the dataset; keeping the old offset makes a successful
    // filter change look like it did nothing when the user was deep in the grid.
    // Tracked against the previous value rather than keyed on the current one,
    // because a LaunchedEffect also runs when the screen re-enters composition
    // — which is what threw the user back to the top on returning from a photo.
    var lastAppliedMediaType by rememberSaveable { mutableStateOf(uiState.mediaType) }
    LaunchedEffect(uiState.mediaType) {
        if (uiState.mediaType != lastAppliedMediaType) {
            lastAppliedMediaType = uiState.mediaType
            gridState.scrollToItem(0)
        }
    }

    LaunchedEffect(uiState.records.isNotEmpty()) {
        if (uiState.records.isNotEmpty()) {
            withFrameNanos { viewModel.onFirstContentDrawn() }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = "Iris",
                            fontSize = 22.sp,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onBackground
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        ServerStatusBadge(
                            serverInfo = uiState.serverInfo,
                            isConnecting = uiState.isServerChecking,
                            isServerOnline = uiState.isServerOnline,
                            totalRecords = if (uiState.totalRecords > 0) uiState.totalRecords else uiState.records.size,
                            isDeviceLoggedIn = uiState.isDeviceLoggedIn,
                            onClick = onSettingsClick
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(
                            imageVector = Icons.Default.Refresh,
                            contentDescription = "Atualizar"
                        )
                    }
                    IconButton(onClick = onSettingsClick) {
                        Icon(
                            imageVector = Icons.Default.Settings,
                            contentDescription = "Configurações"
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = IrisDarkBg
                )
            )
        },
        containerColor = IrisDarkBg
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            // Media type filter chips
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                FilterChip(
                    selected = uiState.mediaType == "all",
                    onClick = { viewModel.setMediaType("all") },
                    label = { Text("Todas") },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccentLime,
                        selectedLabelColor = IrisAccentInk
                    )
                )
                FilterChip(
                    selected = uiState.mediaType == "image",
                    onClick = { viewModel.setMediaType("image") },
                    label = { Text("Imagens") },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccentLime,
                        selectedLabelColor = IrisAccentInk
                    )
                )
                FilterChip(
                    selected = uiState.mediaType == "video",
                    onClick = { viewModel.setMediaType("video") },
                    label = { Text("Vídeos") },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = IrisAccentLime,
                        selectedLabelColor = IrisAccentInk
                    )
                )
            }

            if (!uiState.deviceMediaPermissionGranted) {
                TextButton(onClick = { mediaPermissionLauncher.launch(requestedMediaPermissions) }) {
                    Text("Mostrar fotos deste aparelho")
                }
            }

            if (uiState.error == "AUTH_REQUIRED" && uiState.records.any { it.deviceUri != null }) {
                Text(
                    text = uiState.signOutReason?.let { "$it Enquanto isso, você vê as fotos do aparelho." }
                        ?: "Fotos do aparelho. Entre para ver também as fotos do servidor Iris.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
                )
            }

            if (uiState.isServerOnline == false ||
                uiState.cloudSyncStatus.connectionState == CloudConnectionState.OFFLINE ||
                uiState.cloudSyncStatus.syncError != null
            ) {
                CloudSyncNotice(
                    status = uiState.cloudSyncStatus,
                    serverUnavailable = uiState.isServerOnline == false,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                )
            }

            PullToRefreshBox(
                isRefreshing = uiState.isRefreshing,
                onRefresh = { viewModel.refresh() },
                modifier = Modifier.fillMaxSize()
            ) {
                when {
                    uiState.error == "AUTH_REQUIRED" && uiState.records.isEmpty() -> {
                        EmptyState(
                            icon = Icons.Default.Lock,
                            title = "Login Necessário",
                            message = uiState.signOutReason
                                ?: "Servidor conectado! Para visualizar sua biblioteca privada, autentique este dispositivo.",
                            actionLabel = "Fazer Login",
                            onAction = onLoginClick
                        )
                    }

                    (uiState.isLoading || uiState.isServerChecking) && uiState.records.isEmpty() -> {
                        GalleryLoadingGrid()
                    }

                    uiState.error != null && uiState.records.isEmpty() -> {
                        EmptyState(
                            title = "Não foi possível conectar",
                            message = if (uiState.error == "SERVER_OFFLINE") {
                                "Não foi possível alcançar o servidor Iris. Verifique se ele está rodando e a URL em Configurações."
                            } else {
                                uiState.error ?: "Verifique se o servidor Iris está em execução."
                            },
                            actionLabel = "Tentar novamente",
                            onAction = { viewModel.checkServerAndLoad() }
                        )
                    }

                    uiState.records.isEmpty() -> {
                        EmptyState(
                            icon = Icons.Outlined.PhotoLibrary,
                            title = if (uiState.deviceMediaPermissionGranted) "Nenhuma mídia encontrada" else "Fotos do aparelho não disponíveis",
                            message = if (uiState.deviceMediaPermissionGranted) {
                                "Nenhuma foto ou vídeo neste aparelho ou na biblioteca Iris."
                            } else {
                                "Permita acesso às fotos e vídeos para mostrá-los junto com a biblioteca Iris."
                            },
                            actionLabel = if (uiState.deviceMediaPermissionGranted) "Atualizar" else "Permitir acesso às fotos",
                            onAction = {
                                if (uiState.deviceMediaPermissionGranted) viewModel.refresh()
                                else mediaPermissionLauncher.launch(requestedMediaPermissions)
                            }
                        )
                    }

                    else -> {
                        val sections = remember(uiState.records) { groupByDate(uiState.records) }
                        Box(modifier = Modifier.fillMaxSize()) {
                            LazyVerticalGrid(
                                // Fixed column count driven by pinch-to-zoom (Google
                                // Photos style) instead of Adaptive — the span needs
                                // to be known up front to size date-header rows
                                // correctly.
                                columns = GridCells.Fixed(columnCount),
                                state = gridState,
                                contentPadding = PaddingValues(8.dp),
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp),
                                modifier = Modifier
                                    .fillMaxSize()
                                    .pinchToZoomColumns(
                                        columnCount = columnCount,
                                        onColumnCountChange = { columnCount = it }
                                    )
                            ) {
                                sections.forEach { section ->
                                    item(
                                        key = "header-${section.label}",
                                        contentType = "date-header",
                                        span = { GridItemSpan(maxLineSpan) }
                                    ) {
                                        DateSectionHeader(section.label)
                                    }
                                    items(
                                        items = section.records,
                                        key = { record -> record.index },
                                        contentType = { "media" }
                                    ) { record ->
                                        MediaCard(
                                            record = record,
                                            performanceMonitor = viewModel.performanceMonitor,
                                            origin = uiState.origins.originOf(record, uiState.backupPolicy),
                                            onClick = {
                                                val localUri = record.deviceUri
                                                if (localUri != null) onDeviceMediaClick(localUri)
                                                else onMediaClick(record.index)
                                            },
                                            // Animates position/size when the pinch
                                            // gesture changes columnCount instead of
                                            // the grid reflowing in a single abrupt
                                            // frame.
                                            modifier = Modifier.animateItem()
                                        )
                                    }
                                }

                                if (uiState.isLoading && uiState.records.isNotEmpty()) {
                                    items(12, contentType = { "placeholder" }) {
                                        GalleryPreviewSkeleton()
                                    }
                                }

                                // Outrunning the download used to look exactly
                                // like reaching the end of the library: the grid
                                // simply stopped, with nothing to say more was
                                // coming. This states it, and says how much.
                                if (uiState.records.size < uiState.totalRecords) {
                                    item(
                                        key = "loading-more",
                                        contentType = "loading-footer",
                                        span = { GridItemSpan(maxLineSpan) }
                                    ) {
                                        LoadingMoreFooter(
                                            loaded = uiState.records.size,
                                            total = uiState.totalRecords
                                        )
                                    }
                                }
                            }

                            if (uiState.totalRecords > 40) {
                                FastScrollbar(
                                    gridState = gridState,
                                    sections = sections,
                                    modifier = Modifier
                                        .align(Alignment.CenterEnd)
                                        .fillMaxHeight()
                                        .padding(vertical = 8.dp)
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun GalleryLoadingGrid() {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(150.dp),
        contentPadding = PaddingValues(8.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
        modifier = Modifier.fillMaxSize()
    ) {
        items(18, contentType = { "placeholder" }) { GalleryPreviewSkeleton() }
    }
}

@Composable
private fun GalleryPreviewSkeleton() {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(1f)
            .clip(RoundedCornerShape(12.dp))
            .background(IrisDarkSurface)
    )
}

@Composable
private fun LoadingMoreFooter(loaded: Int, total: Int) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 20.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically
    ) {
        CircularProgressIndicator(
            modifier = Modifier.size(16.dp),
            strokeWidth = 2.dp,
            color = IrisAccentLime
        )
        Spacer(modifier = Modifier.width(10.dp))
        Text(
            text = "Carregando mais… $loaded de $total",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

@Composable
private fun DateSectionHeader(label: String) {
    Text(
        text = label,
        fontSize = 15.sp,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onBackground,
        modifier = Modifier.padding(top = 10.dp, bottom = 2.dp)
    )
}

private data class DateSection(val label: String, val records: List<MediaRecord>)

private val dateSectionZone: ZoneId = ZoneId.systemDefault()
private val dateSectionSameYearFormatter =
    DateTimeFormatter.ofPattern("d 'de' MMMM", Locale("pt", "BR"))
private val dateSectionOtherYearFormatter =
    DateTimeFormatter.ofPattern("d 'de' MMMM 'de' yyyy", Locale("pt", "BR"))

/**
 * Buckets records into Google-Photos-style date sections. Relies on records
 * already arriving newest-first (server sort_by=data) — this only groups
 * consecutive same-day items, it does not re-sort them.
 */
private fun groupByDate(records: List<MediaRecord>): List<DateSection> {
    if (records.isEmpty()) return emptyList()
    val today = LocalDate.now(dateSectionZone)
    val yesterday = today.minusDays(1)

    fun labelFor(record: MediaRecord): String {
        val mtime = record.fileMtime ?: return "Data desconhecida"
        val day = Instant.ofEpochSecond(mtime.toLong()).atZone(dateSectionZone).toLocalDate()
        return when (day) {
            today -> "Hoje"
            yesterday -> "Ontem"
            else -> if (day.year == today.year) {
                day.format(dateSectionSameYearFormatter)
            } else {
                day.format(dateSectionOtherYearFormatter)
            }
        }
    }

    val sections = mutableListOf<DateSection>()
    var currentLabel: String? = null
    var currentBucket = mutableListOf<MediaRecord>()
    for (record in records) {
        val label = labelFor(record)
        if (label != currentLabel) {
            if (currentBucket.isNotEmpty()) {
                sections += DateSection(currentLabel!!, currentBucket)
            }
            currentLabel = label
            currentBucket = mutableListOf()
        }
        currentBucket += record
    }
    if (currentBucket.isNotEmpty()) {
        sections += DateSection(currentLabel!!, currentBucket)
    }
    return sections
}

/**
 * Google-Photos-style fast-scroll rail on the trailing edge: drag anywhere on
 * it to jump through the whole gallery instead of flinging repeatedly, with a
 * date bubble showing where a release would land. Fades in on scroll/drag and
 * out after a second of inactivity so it doesn't sit on screen permanently.
 */
@Composable
private fun FastScrollbar(
    gridState: LazyGridState,
    sections: List<DateSection>,
    modifier: Modifier = Modifier
) {
    if (sections.isEmpty()) return
    val coroutineScope = rememberCoroutineScope()
    val density = LocalDensity.current

    // Mirrors the LazyGridScope item order built above (1 header slot + one
    // grid item per record per section) so a drag fraction maps back to the
    // same date the grid actually laid out at that position.
    val sectionFlatCounts = remember(sections) { sections.map { 1 + it.records.size } }
    val totalFlatItems = remember(sectionFlatCounts) { sectionFlatCounts.sum().coerceAtLeast(1) }

    var isDragging by remember { mutableStateOf(false) }
    var dragFraction by remember { mutableFloatStateOf(0f) }
    var trackHeightPx by remember { mutableFloatStateOf(0f) }
    val thumbHeightPx = with(density) { 32.dp.toPx() }

    val scrollFraction by remember {
        derivedStateOf {
            val total = gridState.layoutInfo.totalItemsCount
            if (total <= 1) 0f
            else (gridState.firstVisibleItemIndex.toFloat() / (total - 1).toFloat()).coerceIn(0f, 1f)
        }
    }

    // Keyed on isScrollInProgress (flips only at gesture start/stop) rather
    // than scrollFraction, which changes on nearly every frame while
    // scrolling — keying the effect on that relaunched a coroutine per frame.
    var recentlyScrolled by remember { mutableStateOf(false) }
    LaunchedEffect(gridState.isScrollInProgress) {
        if (gridState.isScrollInProgress) {
            recentlyScrolled = true
        } else {
            delay(1200)
            recentlyScrolled = false
        }
    }
    val thumbAlpha by animateFloatAsState(
        targetValue = if (isDragging || recentlyScrolled) 1f else 0f,
        label = "scrubberAlpha"
    )

    // Read the moving position during placement, not composition. Ordinary
    // scrolling should move the thumb without rebuilding its content.
    fun activeFraction() = if (isDragging) dragFraction else scrollFraction
    fun thumbY() = (trackHeightPx * activeFraction() - thumbHeightPx / 2)
        .coerceIn(0f, (trackHeightPx - thumbHeightPx).coerceAtLeast(0f))
    val activeLabel = if (isDragging) {
        labelForFraction(sections, sectionFlatCounts, totalFlatItems, dragFraction)
    } else {
        ""
    }

    fun seekTo(y: Float) {
        val fraction = (y / trackHeightPx.coerceAtLeast(1f)).coerceIn(0f, 1f)
        dragFraction = fraction
        val total = gridState.layoutInfo.totalItemsCount
        if (total > 0) {
            val targetIndex = (fraction * (total - 1)).roundToInt().coerceIn(0, total - 1)
            coroutineScope.launch { gridState.scrollToItem(targetIndex) }
        }
    }
    // Keep the long-lived gesture handler bound to the current paginated grid.
    val latestSeekTo by rememberUpdatedState<(Float) -> Unit>(::seekTo)

    Box(
        modifier = modifier
            .width(32.dp)
            .onSizeChanged { trackHeightPx = it.height.toFloat() }
            .pointerInput(Unit) {
                detectDragGestures(
                    onDragStart = { offset ->
                        isDragging = true
                        latestSeekTo(offset.y)
                    },
                    onDrag = { change, _ ->
                        change.consume()
                        latestSeekTo(change.position.y)
                    },
                    onDragEnd = { isDragging = false },
                    onDragCancel = { isDragging = false }
                )
            }
    ) {
        if (isDragging) {
            Box(
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .offset {
                        IntOffset(
                            x = -32.dp.roundToPx(),
                            y = (thumbY() + thumbHeightPx / 2 - 16.dp.toPx()).roundToInt()
                        )
                    }
                    .background(IrisAccentLime, RoundedCornerShape(8.dp))
                    .padding(horizontal = 10.dp, vertical = 6.dp)
            ) {
                Text(
                    text = activeLabel,
                    color = IrisAccentInk,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1
                )
            }
        }

        Box(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .graphicsLayer { alpha = thumbAlpha }
                .offset { IntOffset(x = 0, y = thumbY().roundToInt()) }
                .width(4.dp)
                .height(32.dp)
                .background(IrisAccentLime, RoundedCornerShape(2.dp))
        )
    }
}

private fun labelForFraction(
    sections: List<DateSection>,
    flatCounts: List<Int>,
    totalFlatItems: Int,
    fraction: Float
): String {
    val targetIndex = (fraction * totalFlatItems).toInt().coerceIn(0, totalFlatItems - 1)
    var cumulative = 0
    for (i in flatCounts.indices) {
        cumulative += flatCounts[i]
        if (targetIndex < cumulative) return sections[i].label
    }
    return sections.last().label
}

/**
 * Two-finger pinch changes the grid's column count (fewer columns = bigger
 * previews), the same gesture Google Photos uses. Only reacts once 2+
 * pointers are down and consumes events solely in that case, so a normal
 * one-finger drag keeps scrolling the grid untouched.
 */
private fun Modifier.pinchToZoomColumns(
    columnCount: Int,
    onColumnCountChange: (Int) -> Unit,
    minColumns: Int = 2,
    maxColumns: Int = 6
): Modifier = composed {
    // The gesture coroutine below is launched once (key = Unit) and lives across
    // recompositions; rememberUpdatedState lets it see the latest column count
    // and callback instead of the stale values captured at launch time — without
    // this, restarting the pointerInput on every column change would drop
    // fingers still on screen and turn a smooth pinch into single stepped taps.
    val latestColumnCount by rememberUpdatedState(columnCount)
    val latestOnColumnCountChange by rememberUpdatedState(onColumnCountChange)
    pointerInput(Unit) {
        val zoomOutThreshold = 1.25f
        val zoomInThreshold = 0.8f
        awaitEachGesture {
            var zoomAccumulator = 1f
            var pinching = false
            do {
                val event = awaitPointerEvent()
                val activePointers = event.changes.count { it.pressed }
                if (activePointers >= 2) {
                    pinching = true
                    zoomAccumulator *= event.calculateZoom()
                    event.changes.forEach { it.consume() }
                    if (zoomAccumulator > zoomOutThreshold) {
                        val next = (latestColumnCount - 1).coerceAtLeast(minColumns)
                        if (next != latestColumnCount) latestOnColumnCountChange(next)
                        zoomAccumulator = 1f
                    } else if (zoomAccumulator < zoomInThreshold) {
                        val next = (latestColumnCount + 1).coerceAtMost(maxColumns)
                        if (next != latestColumnCount) latestOnColumnCountChange(next)
                        zoomAccumulator = 1f
                    }
                } else if (pinching) {
                    // A pinch that drops back to one finger shouldn't hand off
                    // into a drag-scroll using the pinch's leftover finger.
                    event.changes.forEach { it.consume() }
                }
            } while (event.changes.any { it.pressed })
        }
    }
}

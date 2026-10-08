package com.iris.app.ui.screens.detail

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Velocity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.ui.theme.IrisTheme
import kotlinx.coroutines.launch

/** One action of a viewer bar or menu. */
internal data class ViewerAction(val icon: ImageVector, val label: String, val onClick: () -> Unit)

/** Share of the screen height the information panel opens at. */
private const val PANEL_BASE_FRACTION = 0.68f

/** How far dragging up grows it: a strip of the photo, with the back button, stays in view. */
private const val PANEL_MAX_FRACTION = 0.86f

/**
 * The layout every viewer shares, as a phone gallery does it: the photo
 * full-bleed with its bars over it, and an information panel that rises from
 * the bottom and pushes the photo up instead of covering it.
 *
 * Dragging anywhere on the panel moves it: up grows it until it nearly covers
 * the photo, down (once its content is back at the top) shrinks it and then
 * pulls it away to close it ([PanelDrag]). A back button above it, the
 * handle and the system back key close it too.
 */
@Composable
internal fun ViewerScaffold(
    panelOpen: Boolean,
    onClosePanel: () -> Unit,
    chromeVisible: Boolean,
    topBar: @Composable () -> Unit,
    bottomBar: @Composable () -> Unit,
    panel: @Composable () -> Unit,
    photo: @Composable (Modifier) -> Unit,
) {
    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
    ) {
        val density = LocalDensity.current
        val fullHeight = constraints.maxHeight.toFloat()
        val baseHeight = fullHeight * PANEL_BASE_FRACTION
        val drag = remember(fullHeight) {
            PanelDrag(
                maxExpansion = fullHeight * (PANEL_MAX_FRACTION - PANEL_BASE_FRACTION),
                closeDistance = with(density) { 96.dp.toPx() },
            )
        }
        val scope = rememberCoroutineScope()
        BackHandler(enabled = panelOpen) { onClosePanel() }
        val progress by animateFloatAsState(if (panelOpen) 1f else 0f, label = "info panel")
        // Back at rest once fully closed, so the next opening starts from the base height.
        LaunchedEffect(progress == 0f) { if (progress == 0f) drag.reset() }

        fun release(velocity: Float) {
            if (drag.release(velocity)) {
                onClosePanel()
            } else if (drag.pull > 0f) {
                val from = drag.pull
                scope.launch { animate(from, 0f) { value, _ -> drag.settlePull(value) } }
            }
        }

        val connection = remember(drag) {
            object : NestedScrollConnection {
                override fun onPreScroll(available: Offset, source: NestedScrollSource): Offset =
                    if (source == NestedScrollSource.UserInput) Offset(0f, drag.beforeScroll(available.y)) else Offset.Zero

                override fun onPostScroll(consumed: Offset, available: Offset, source: NestedScrollSource): Offset =
                    if (source == NestedScrollSource.UserInput) Offset(0f, drag.afterScroll(available.y)) else Offset.Zero

                override suspend fun onPreFling(available: Velocity): Velocity {
                    if (drag.pull <= 0f) return Velocity.Zero
                    release(available.y)
                    return available
                }
            }
        }

        val panelHeight = baseHeight + drag.expansion
        val panelShown = (panelHeight * progress - drag.pull).coerceIn(0f, fullHeight)
        photo(
            Modifier
                .fillMaxWidth()
                .height(with(density) { (fullHeight - panelShown).toDp() })
                .align(Alignment.TopCenter)
        )

        AnimatedVisibility(
            visible = chromeVisible && !panelOpen,
            enter = fadeIn() + slideInVertically { -it },
            exit = fadeOut() + slideOutVertically { -it },
            modifier = Modifier.align(Alignment.TopCenter)
        ) { topBar() }

        AnimatedVisibility(
            visible = chromeVisible && !panelOpen,
            enter = fadeIn() + slideInVertically { it },
            exit = fadeOut() + slideOutVertically { it },
            modifier = Modifier.align(Alignment.BottomCenter)
        ) { bottomBar() }

        if (progress > 0f) {
            // The photo stays on black; the panel follows the system's light or dark setting.
            IrisTheme(darkTheme = isSystemInDarkTheme()) {
                Column(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .fillMaxWidth()
                        .height(with(density) { panelHeight.toDp() })
                        .graphicsLayer { translationY = panelHeight * (1f - progress) + drag.pull }
                        .clip(RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp))
                        .background(MaterialTheme.colorScheme.surface)
                        .nestedScroll(connection)
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(28.dp)
                            .pointerInput(drag) {
                                detectVerticalDragGestures(
                                    onDragEnd = { release(0f) },
                                    onDragCancel = { release(0f) },
                                    onVerticalDrag = { change, amount ->
                                        change.consume()
                                        if (amount < 0f) drag.beforeScroll(amount) else drag.afterScroll(amount)
                                    },
                                )
                            },
                        contentAlignment = Alignment.Center
                    ) {
                        Box(
                            Modifier
                                .size(width = 36.dp, height = 4.dp)
                                .background(
                                    MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f),
                                    RoundedCornerShape(2.dp)
                                )
                        )
                    }
                    panel()
                }
            }
            // The way back stays in reach above the panel.
            IconButton(
                onClick = onClosePanel,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .statusBarsPadding()
                    .padding(8.dp)
                    .size(48.dp)
                    .background(Color.Black.copy(alpha = 0.55f), CircleShape)
            ) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Fechar informações", tint = Color.White)
            }
        }
    }
}

/**
 * Back, the date centred as a gallery shows it (a tap, or the ›, opens the
 * information panel when [onTitleClick] is given), and [menu] behind ⋮.
 */
@Composable
internal fun ViewerTopBar(
    title: ViewerTitle,
    onBack: () -> Unit,
    onTitleClick: (() -> Unit)?,
    menu: List<ViewerAction>,
) {
    var menuOpen by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color.Black.copy(alpha = 0.55f))
            .pointerInput(Unit) { detectTapGestures { } }
            .statusBarsPadding()
            .padding(horizontal = 4.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButton(onClick = onBack, modifier = Modifier.size(52.dp)) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Voltar", tint = Color.White)
        }
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier
                .weight(1f)
                .clip(RoundedCornerShape(12.dp))
                .then(if (onTitleClick != null) Modifier.clickable(onClick = onTitleClick, role = Role.Button) else Modifier)
                .padding(vertical = 2.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = title.headline,
                    color = Color.White,
                    fontSize = 17.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false)
                )
                if (onTitleClick != null) {
                    Icon(
                        Icons.AutoMirrored.Filled.KeyboardArrowRight,
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(20.dp)
                    )
                }
            }
            title.subline?.let {
                Text(
                    text = it,
                    color = Color.White.copy(alpha = 0.8f),
                    fontSize = 14.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }
        Box {
            IconButton(
                onClick = { menuOpen = true },
                enabled = menu.isNotEmpty(),
                modifier = Modifier.size(52.dp)
            ) {
                Icon(Icons.Default.MoreVert, contentDescription = "Mais opções", tint = Color.White)
            }
            DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                menu.forEach { action ->
                    DropdownMenuItem(
                        text = { Text(action.label, fontSize = 17.sp) },
                        leadingIcon = { Icon(action.icon, contentDescription = null) },
                        onClick = {
                            menuOpen = false
                            action.onClick()
                        },
                        modifier = Modifier.height(56.dp)
                    )
                }
            }
        }
    }
}

/** The everyday actions, where a gallery app has them: each a large icon with its name under it. */
@Composable
internal fun ViewerActionBar(actions: List<ViewerAction>) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color.Black.copy(alpha = 0.55f))
            // Without this, a tap between two buttons reaches the photo and hides
            // the controls, which looks like the button failed.
            .pointerInput(Unit) { detectTapGestures { } }
            .navigationBarsPadding()
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.Top
    ) {
        actions.forEach { ActionItem(it) }
    }
}

@Composable
private fun RowScope.ActionItem(action: ViewerAction) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Top,
        modifier = Modifier
            .weight(1f)
            .heightIn(min = 64.dp)
            .clip(RoundedCornerShape(12.dp))
            .clickable(onClick = action.onClick, role = Role.Button)
            .padding(vertical = 6.dp, horizontal = 2.dp)
    ) {
        Icon(action.icon, contentDescription = null, tint = Color.White, modifier = Modifier.size(26.dp))
        Spacer(Modifier.height(4.dp))
        Text(
            action.label,
            color = Color.White,
            fontSize = 13.sp,
            maxLines = 2,
            textAlign = TextAlign.Center,
            lineHeight = 15.sp
        )
    }
}

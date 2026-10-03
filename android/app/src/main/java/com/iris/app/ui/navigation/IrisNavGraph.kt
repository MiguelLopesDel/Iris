package com.iris.app.ui.navigation

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.outlined.Face
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.outlined.Group
import androidx.compose.material.icons.outlined.PhotoLibrary
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.withFrameNanos
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.navArgument
import com.iris.app.IrisApplication
import com.iris.app.data.sync.MediaSyncWorker
import com.iris.app.ui.screens.collections.CollectionMediaScreen
import com.iris.app.ui.screens.collections.CollectionMediaViewModel
import com.iris.app.ui.screens.collections.CollectionsScreen
import com.iris.app.ui.screens.collections.CollectionsViewModel
import com.iris.app.ui.screens.detail.MediaDetailScreen
import com.iris.app.ui.screens.detail.MediaDetailViewModel
import com.iris.app.ui.screens.gallery.GalleryScreen
import com.iris.app.ui.screens.gallery.GalleryViewModel
import com.iris.app.ui.screens.gallery.LocalMediaViewerScreen
import com.iris.app.ui.screens.gallery.rememberNotInBackupNotice
import com.iris.app.ui.screens.persons.PersonMediaScreen
import com.iris.app.ui.screens.persons.PersonMediaViewModel
import com.iris.app.ui.screens.persons.PersonsScreen
import com.iris.app.ui.screens.persons.PersonsViewModel
import com.iris.app.ui.screens.search.SearchScreen
import com.iris.app.ui.screens.search.SearchViewModel
import com.iris.app.ui.screens.spaces.SpaceScreen
import com.iris.app.ui.screens.spaces.SpaceViewModel
import com.iris.app.ui.screens.spaces.SpacesScreen
import com.iris.app.ui.screens.spaces.SpacesViewModel
import com.iris.app.ui.screens.sync.SyncScreen
import com.iris.app.ui.screens.sync.SyncViewModel
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.outlined.CloudUpload
import com.iris.app.ui.screens.settings.SettingsScreen
import com.iris.app.ui.screens.settings.SettingsViewModel
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDarkBg
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.performance.Metric

data class BottomNavItem(
    val route: String,
    val label: String,
    val selectedIcon: ImageVector,
    val unselectedIcon: ImageVector
)

@Composable
fun IrisNavGraph(
    navController: NavHostController,
    application: IrisApplication
) {
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = navBackStackEntry?.destination
    val navigationFinishes = remember { mutableMapOf<String, () -> Unit>() }

    // A pairing link opened from outside the app is confirmed in Settings.
    val pendingPairing by application.pairingRequests.collectAsStateWithLifecycle()
    LaunchedEffect(pendingPairing) {
        if (pendingPairing != null && currentDestination?.route != NavRoute.Settings.route) {
            navController.navigate(NavRoute.Settings.route) { launchSingleTop = true }
        }
    }
    val previousSession = remember(application.credentialsStore) {
        mutableStateOf(application.credentialsStore.sessionIdentity.value)
    }

    // Leaving a private screen immediately on logout prevents an already-open
    // viewer/detail route from continuing to display authenticated media.
    LaunchedEffect(application.credentialsStore) {
        application.credentialsStore.sessionIdentity.collect { identity ->
            val changed = identity != previousSession.value
            previousSession.value = identity
            val route = navController.currentDestination?.route
            if ((changed || identity == null) && route != null && route !in setOf(
                    NavRoute.Gallery.route,
                    NavRoute.Sync.route,
                    NavRoute.Settings.route,
            )
            ) {
                withContext(Dispatchers.Main.immediate) {
                    navController.navigate(NavRoute.Gallery.route) {
                        popUpTo(navController.graph.findStartDestination().id) {
                            inclusive = false
                            saveState = false
                        }
                        launchSingleTop = true
                        restoreState = false
                    }
                }
            }
        }
    }

    val bottomNavItems = listOf(
        BottomNavItem(
            route = NavRoute.Gallery.route,
            label = "Galeria",
            selectedIcon = Icons.Filled.PhotoLibrary,
            unselectedIcon = Icons.Outlined.PhotoLibrary
        ),
        BottomNavItem(
            route = NavRoute.Search.route,
            label = "Busca",
            selectedIcon = Icons.Filled.Search,
            unselectedIcon = Icons.Outlined.Search
        ),
        BottomNavItem(
            route = NavRoute.Collections.route,
            label = "Álbuns",
            selectedIcon = Icons.Filled.Folder,
            unselectedIcon = Icons.Outlined.Folder
        ),
        BottomNavItem(
            route = NavRoute.Spaces.route,
            label = "Espaços",
            selectedIcon = Icons.Filled.Group,
            unselectedIcon = Icons.Outlined.Group
        ),
        BottomNavItem(
            route = NavRoute.Sync.route,
            label = "Sincronizar",
            selectedIcon = Icons.Filled.CloudUpload,
            unselectedIcon = Icons.Outlined.CloudUpload
        )
    )

    LaunchedEffect(currentDestination?.route) {
        val route = currentDestination?.route ?: return@LaunchedEffect
        withFrameNanos {
            navigationFinishes.remove(route)?.invoke()
        }
    }

    // Hide bottom navigation bar on detail screens or settings
    val showBottomBar = bottomNavItems.any { it.route == currentDestination?.route }

    Scaffold(
        bottomBar = {
            if (showBottomBar) {
                NavigationBar(
                    containerColor = IrisDarkSurface,
                    contentColor = IrisAccentLime
                ) {
                    bottomNavItems.forEach { item ->
                        val selected = currentDestination?.route == item.route
                        NavigationBarItem(
                            selected = selected,
                            onClick = {
                                if (selected) return@NavigationBarItem
                                navigationFinishes[item.route] = application.performanceMonitor.begin(
                                    item.navigationMetric()
                                )
                                navController.navigate(item.route) {
                                    popUpTo(navController.graph.findStartDestination().id) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = {
                                Icon(
                                    imageVector = if (selected) item.selectedIcon else item.unselectedIcon,
                                    contentDescription = item.label
                                )
                            },
                            label = { Text(item.label) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = IrisAccentInk,
                                selectedTextColor = IrisAccentLime,
                                indicatorColor = IrisAccentLime,
                                unselectedIconColor = IrisTextSoft,
                                unselectedTextColor = IrisTextMuted
                            )
                        )
                    }
                }
            }
        },
        containerColor = IrisDarkBg
    ) { paddingValues ->
        NavHost(
            navController = navController,
            startDestination = NavRoute.Gallery.route,
            modifier = Modifier.padding(paddingValues)
        ) {
            composable(NavRoute.Gallery.route) {
                val viewModel: GalleryViewModel = viewModel(
                    factory = GalleryViewModel.Factory(
                        application.irisRepository,
                        application.performanceMonitor,
                        application.galleryDataSource,
                        application.settingsRepository
                    )
                )
                GalleryScreen(
                    viewModel = viewModel,
                    onMediaClick = { index ->
                        navController.navigate(NavRoute.Detail.createRoute(index))
                    },
                    onDeviceMediaClick = { uri ->
                        navController.navigate(NavRoute.LocalMediaDetail.createRoute(uri))
                    },
                    onSettingsClick = {
                        navController.navigate(NavRoute.Settings.route)
                    },
                    onLoginClick = {
                        navController.navigate(NavRoute.Sync.route)
                    }
                )
            }

            composable(
                route = NavRoute.LocalMediaDetail.route,
                arguments = listOf(navArgument("mediaUri") { type = NavType.StringType })
            ) { backStackEntry ->
                val mediaUri = backStackEntry.arguments?.getString("mediaUri").orEmpty()
                LocalMediaViewerScreen(
                    mediaUri = mediaUri,
                    onBack = { navController.popBackStack() },
                    notInBackup = rememberNotInBackupNotice(application, mediaUri),
                )
            }

            composable(NavRoute.Search.route) {
                val viewModel: SearchViewModel = viewModel(
                    factory = SearchViewModel.Factory(application.irisRepository)
                )
                SearchScreen(
                    viewModel = viewModel,
                    onMediaClick = { index ->
                        navController.navigate(NavRoute.Detail.createRoute(index))
                    }
                )
            }

            composable(NavRoute.Persons.route) {
                val viewModel: PersonsViewModel = viewModel(
                    factory = PersonsViewModel.Factory(application.irisRepository)
                )
                PersonsScreen(
                    viewModel = viewModel,
                    onPersonClick = { personId, personName ->
                        navController.navigate(NavRoute.PersonMedia.createRoute(personId, personName))
                    }
                )
            }

            composable(
                route = NavRoute.PersonMedia.route,
                arguments = listOf(
                    navArgument("personId") { type = NavType.IntType },
                    navArgument("personName") {
                        type = NavType.StringType
                        defaultValue = ""
                    }
                )
            ) { backStackEntry ->
                val personId = backStackEntry.arguments?.getInt("personId") ?: 0
                val personName = backStackEntry.arguments?.getString("personName") ?: ""
                val viewModel: PersonMediaViewModel = viewModel(
                    key = "person_$personId",
                    factory = PersonMediaViewModel.Factory(personId, personName, application.irisRepository)
                )
                PersonMediaScreen(
                    viewModel = viewModel,
                    onBack = { navController.popBackStack() },
                    onMediaClick = { index ->
                        navController.navigate(NavRoute.Detail.createRoute(index))
                    }
                )
            }

            composable(NavRoute.Collections.route) {
                val viewModel: CollectionsViewModel = viewModel(
                    factory = CollectionsViewModel.Factory(application.irisRepository)
                )
                CollectionsScreen(
                    viewModel = viewModel,
                    onCollectionClick = { colId, colName ->
                        navController.navigate(NavRoute.CollectionMedia.createRoute(colId, colName))
                    },
                    onPeopleClick = { navController.navigate(NavRoute.Persons.route) }
                )
            }

            composable(
                route = NavRoute.CollectionMedia.route,
                arguments = listOf(
                    navArgument("collectionId") { type = NavType.IntType },
                    navArgument("collectionName") {
                        type = NavType.StringType
                        defaultValue = ""
                    }
                )
            ) { backStackEntry ->
                val colId = backStackEntry.arguments?.getInt("collectionId") ?: 0
                val colName = backStackEntry.arguments?.getString("collectionName") ?: ""
                val viewModel: CollectionMediaViewModel = viewModel(
                    key = "col_$colId",
                    factory = CollectionMediaViewModel.Factory(
                        colId,
                        colName,
                        application.irisRepository,
                        application.performanceMonitor
                    )
                )
                CollectionMediaScreen(
                    viewModel = viewModel,
                    onBack = { navController.popBackStack() },
                    onMediaClick = { index ->
                        navController.navigate(NavRoute.Detail.createRoute(index))
                    }
                )
            }

            composable(
                route = NavRoute.Detail.route,
                arguments = listOf(navArgument("recordIndex") { type = NavType.IntType })
            ) { backStackEntry ->
                val recordIndex = backStackEntry.arguments?.getInt("recordIndex") ?: 0
                val viewModel: MediaDetailViewModel = viewModel(
                    key = "detail_$recordIndex",
                    factory = MediaDetailViewModel.Factory(recordIndex, application.irisRepository)
                )
                MediaDetailScreen(
                    viewModel = viewModel,
                    onBack = { navController.popBackStack() },
                    onMediaClick = { index ->
                        navController.navigate(NavRoute.Detail.createRoute(index))
                    },
                    onPersonClick = { personId, personName ->
                        navController.navigate(NavRoute.PersonMedia.createRoute(personId, personName))
                    }
                )
            }

            composable(NavRoute.Spaces.route) {
                val viewModel: SpacesViewModel = viewModel(
                    factory = SpacesViewModel.Factory(application.irisRepository)
                )
                SpacesScreen(
                    viewModel = viewModel,
                    onSpaceClick = { spaceId, spaceName ->
                        navController.navigate(NavRoute.Space.createRoute(spaceId, spaceName))
                    }
                )
            }

            composable(
                route = NavRoute.Space.route,
                arguments = listOf(
                    navArgument("spaceId") { type = NavType.IntType },
                    navArgument("spaceName") {
                        type = NavType.StringType
                        defaultValue = ""
                    }
                )
            ) { backStackEntry ->
                val spaceId = backStackEntry.arguments?.getInt("spaceId") ?: 0
                val spaceName = backStackEntry.arguments?.getString("spaceName") ?: ""
                val viewModel: SpaceViewModel = viewModel(
                    key = "space_$spaceId",
                    factory = SpaceViewModel.Factory(spaceId, spaceName, application.irisRepository)
                )
                SpaceScreen(viewModel = viewModel, onBack = { navController.popBackStack() })
            }

            composable(NavRoute.Sync.route) {
                val viewModel: SyncViewModel = viewModel(
                    factory = SyncViewModel.Factory(
                        application.irisRepository,
                        application.settingsRepository,
                        application.credentialsStore,
                        MediaSyncWorker.retryPending(application),
                    )
                )
                SyncScreen(
                    viewModel = viewModel,
                    onConfigureServer = { navController.navigate(NavRoute.Settings.route) }
                )
            }

            composable(NavRoute.Settings.route) {
                val viewModel: SettingsViewModel = viewModel(
                    factory = SettingsViewModel.Factory(
                        application.settingsRepository,
                        application.irisRepository,
                        application.pairingRequests,
                    )
                )
                SettingsScreen(
                    viewModel = viewModel,
                    performanceMonitor = application.performanceMonitor,
                    onBack = { navController.popBackStack() }
                )
            }
        }
    }
}

private fun BottomNavItem.navigationMetric(): Metric = when (route) {
    NavRoute.Gallery.route -> Metric.NavigationGallery
    NavRoute.Search.route -> Metric.NavigationSearch
    NavRoute.Collections.route -> Metric.NavigationAlbums
    NavRoute.Sync.route -> Metric.NavigationSync
    NavRoute.Spaces.route -> Metric.NavigationSpaces
    else -> Metric.NavigationGallery
}

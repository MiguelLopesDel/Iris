package com.iris.app.ui.screens.documents

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.iris.app.IrisApplication
import com.iris.app.data.model.DocumentKind
import com.iris.app.data.repository.IrisRepository
import com.iris.app.ui.components.EmptyState
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisBackground
import com.iris.app.ui.theme.IrisSurfaceBright
import com.iris.app.ui.theme.IrisTextSoft

/** The kinds of document found among the photos, each as a cover with its count. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DocumentsScreen(
    repository: IrisRepository,
    onBack: () -> Unit,
    onKindClick: (key: String, title: String) -> Unit,
) {
    val apiClient = (LocalContext.current.applicationContext as IrisApplication).apiClient
    var kinds by remember { mutableStateOf<List<DocumentKind>?>(null) }
    var failed by remember { mutableStateOf(false) }
    var attempt by remember { mutableIntStateOf(0) }
    LaunchedEffect(attempt) {
        failed = false
        repository.getDocumentKinds()
            .onSuccess { kinds = it.kinds }
            .onFailure { failed = true }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Documentos") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Voltar")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = IrisBackground),
            )
        },
        containerColor = IrisBackground,
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            val loaded = kinds
            when {
                failed && loaded == null -> EmptyState(
                    title = "Não foi possível carregar os documentos",
                    message = "Verifique a conexão com o servidor.",
                    actionLabel = "Tentar novamente",
                    onAction = { attempt++ },
                )
                loaded == null -> CircularProgressIndicator(color = IrisAccent, modifier = Modifier.align(Alignment.Center))
                loaded.isEmpty() -> EmptyState(
                    icon = Icons.Outlined.Description,
                    title = "Nenhum documento encontrado",
                    message = "Fotos de documentos, boletos e comprovantes aparecem aqui sozinhas.",
                )
                else -> LazyVerticalGrid(
                    columns = GridCells.Fixed(2),
                    contentPadding = PaddingValues(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    items(loaded, key = { it.key }) { kind ->
                        Column(Modifier.clickable { onKindClick(kind.key, kind.title) }) {
                            Box(
                                Modifier
                                    .fillMaxWidth()
                                    .aspectRatio(1f)
                                    .clip(RoundedCornerShape(16.dp))
                                    .background(IrisSurfaceBright),
                                contentAlignment = Alignment.Center,
                            ) {
                                Icon(Icons.Outlined.Description, contentDescription = null, tint = IrisTextSoft)
                                kind.cover?.thumbnailUrl?.let { url ->
                                    AsyncImage(
                                        model = apiClient.resolveThumbnailUrl(url),
                                        contentDescription = null,
                                        contentScale = ContentScale.Crop,
                                        modifier = Modifier.fillMaxSize(),
                                    )
                                }
                            }
                            Text(
                                kind.title,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.padding(top = 6.dp),
                            )
                            Text(
                                if (kind.count == 1) "1 foto" else "${kind.count} fotos",
                                fontSize = 13.sp,
                                color = IrisTextSoft,
                            )
                        }
                    }
                }
            }
        }
    }
}

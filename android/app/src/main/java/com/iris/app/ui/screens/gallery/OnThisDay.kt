package com.iris.app.ui.screens.gallery

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.OnThisDayYear
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import coil.compose.AsyncImage

/** "Há 1 ano", "Há 3 anos": how long ago, said the way a person would. */
fun yearsAgoLabel(yearsAgo: Int): String =
    if (yearsAgo == 1) "Há 1 ano" else "Há $yearsAgo anos"

/**
 * One row of large cards, one per earlier year with photos from today's date,
 * as gallery apps show memories: little height taken from the grid however
 * many years there are. A card opens the viewer over that year's photos.
 */
@Composable
fun OnThisDaySection(
    years: List<OnThisDayYear>,
    onMediaClick: (records: List<MediaRecord>, index: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val apiClient = (context.applicationContext as com.iris.app.IrisApplication).apiClient
    Column(modifier = modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        Text(
            text = "Neste dia",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
        )
        LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            items(years, key = { "memory-${it.year}" }) { year ->
                val cover = year.records.first()
                Box(
                    modifier = Modifier
                        .width(150.dp)
                        .height(210.dp)
                        .clip(RoundedCornerShape(18.dp))
                        .clickable { onMediaClick(year.records, cover.index) },
                ) {
                    AsyncImage(
                        model = apiClient.resolveThumbnailUrl(cover.thumbnailUrl),
                        contentDescription = cover.cleanFilename,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surfaceVariant),
                    )
                    Box(
                        Modifier
                            .fillMaxSize()
                            .background(
                                Brush.verticalGradient(
                                    0.5f to Color.Transparent,
                                    1f to Color.Black.copy(alpha = 0.7f),
                                )
                            )
                    )
                    Column(
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .padding(12.dp),
                    ) {
                        Text(
                            yearsAgoLabel(year.yearsAgo),
                            color = Color.White,
                            fontWeight = FontWeight.Bold,
                            fontSize = 18.sp,
                        )
                        Text(
                            "${year.year} · ${if (year.total == 1) "1 foto" else "${year.total} fotos"}",
                            color = Color.White.copy(alpha = 0.85f),
                            fontSize = 13.sp,
                        )
                    }
                }
            }
        }
    }
}

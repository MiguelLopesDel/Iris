package com.iris.app.ui.screens.gallery

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
import com.iris.app.ui.components.MediaCard

/** "Há 1 ano", "Há 3 anos": how long ago, said the way a person would. */
fun yearsAgoLabel(yearsAgo: Int): String =
    if (yearsAgo == 1) "Há 1 ano" else "Há $yearsAgo anos"

/** One row per earlier year with photos from today's date; tapping opens that year's sequence. */
@Composable
fun OnThisDaySection(
    years: List<OnThisDayYear>,
    onMediaClick: (records: List<MediaRecord>, index: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        Text(
            text = "Neste dia",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
        )
        years.forEach { year ->
            Text(
                text = "${yearsAgoLabel(year.yearsAgo)} · ${year.year}",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
            )
            LazyRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(year.records, key = { "memory-${year.year}-${it.index}" }) { record ->
                    Box(modifier = Modifier.width(110.dp)) {
                        MediaCard(record = record, onClick = { onMediaClick(year.records, record.index) })
                    }
                }
            }
        }
    }
}

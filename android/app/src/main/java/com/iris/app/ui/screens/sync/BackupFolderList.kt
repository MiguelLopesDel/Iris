package com.iris.app.ui.screens.sync

import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.iris.app.R
import com.iris.app.ui.theme.IrisSurfaceBright
import com.iris.app.ui.theme.IrisTextSoft

/**
 * The folder chooser's list, in the way messaging and gallery apps present it:
 * the familiar places first, each with its newest photo, a count and a switch;
 * the many remaining folders folded away until asked for.
 */
internal fun LazyListScope.backupFolderSections(
    sections: BackupFolderSections,
    selectedIds: Set<String>,
    expandedGroups: Set<String>,
    othersExpanded: Boolean,
    onToggleGroupExpanded: (String) -> Unit,
    onToggleOthers: () -> Unit,
    onSetSources: (List<String>, Boolean) -> Unit,
) {
    if (sections.main.isNotEmpty()) {
        item(key = "header-main") { SectionHeader(stringResource(R.string.sync_folders_main)) }
        groupItems(sections.main, selectedIds, expandedGroups, onToggleGroupExpanded, onSetSources)
    }
    if (sections.others.isNotEmpty()) {
        item(key = "header-others") {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(onClick = onToggleOthers)
                    .padding(top = 12.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SectionHeader(
                    pluralStringResource(R.plurals.sync_folders_others, sections.others.size, sections.others.size),
                    modifier = Modifier.weight(1f),
                )
                Icon(if (othersExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore, contentDescription = null)
            }
        }
        if (othersExpanded) {
            groupItems(sections.others, selectedIds, expandedGroups, onToggleGroupExpanded, onSetSources)
        }
    }
}

private fun LazyListScope.groupItems(
    groups: List<BackupFolderGroup>,
    selectedIds: Set<String>,
    expandedGroups: Set<String>,
    onToggleGroupExpanded: (String) -> Unit,
    onSetSources: (List<String>, Boolean) -> Unit,
) {
    groups.forEach { group ->
        val expanded = group.key in expandedGroups
        item(key = group.key) {
            val selection = group.selection(selectedIds)
            FolderRow(
                title = group.title,
                summary = folderSummary(group.photos, group.videos, group.folders.size, selection),
                coverUri = group.coverUri,
                icon = iconOf(group.app),
                checked = selection != GroupSelection.NONE,
                expandable = group.folders.size > 1,
                expanded = expanded,
                onExpand = { onToggleGroupExpanded(group.key) },
                // A partly chosen group turns fully on first: that is what a tap on it means.
                onCheckedChange = { onSetSources(group.sourceIds, selection != GroupSelection.ALL) },
                modifier = Modifier.testTag("backup-folder-${group.key}"),
            )
        }
        if (expanded && group.folders.size > 1) {
            items(group.folders, key = { "${group.key}/${it.key}" }) { folder ->
                val on = folder.sourceIds.all { it in selectedIds }
                FolderRow(
                    title = folder.title,
                    summary = folderSummary(folder.photos, folder.videos, 1, GroupSelection.ALL),
                    coverUri = folder.coverUri,
                    icon = iconOf(folder.app),
                    checked = on,
                    expandable = false,
                    expanded = false,
                    onExpand = {},
                    onCheckedChange = { onSetSources(folder.sourceIds, !on) },
                    modifier = Modifier.padding(start = 28.dp),
                )
            }
        }
    }
}

@Composable
private fun SectionHeader(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        modifier = modifier.padding(top = 8.dp, bottom = 4.dp),
        fontWeight = FontWeight.SemiBold,
        color = IrisTextSoft,
        fontSize = 13.sp,
    )
}

@Composable
private fun folderSummary(photos: Int, videos: Int, folders: Int, selection: GroupSelection): String {
    val parts = buildList {
        if (photos > 0) add(pluralStringResource(R.plurals.sync_folder_photos, photos, photos))
        if (videos > 0) add(pluralStringResource(R.plurals.sync_folder_videos, videos, videos))
        if (folders > 1) add(pluralStringResource(R.plurals.sync_folder_subfolders, folders, folders))
        if (selection == GroupSelection.SOME) add(stringResource(R.string.sync_folder_some_selected))
    }
    return parts.joinToString(" · ")
}

private fun iconOf(app: FolderApp?): ImageVector = when (app) {
    FolderApp.CAMERA -> Icons.Default.CameraAlt
    FolderApp.SCREENSHOTS -> Icons.Default.PhoneAndroid
    FolderApp.SCREEN_RECORDINGS -> Icons.Default.Videocam
    FolderApp.DOWNLOADS -> Icons.Default.Download
    null -> Icons.Default.Folder
    else -> Icons.Default.Chat
}

@Composable
private fun FolderRow(
    title: String,
    summary: String,
    coverUri: String?,
    icon: ImageVector,
    checked: Boolean,
    expandable: Boolean,
    expanded: Boolean,
    onExpand: () -> Unit,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clickable { if (expandable) onExpand() else onCheckedChange(!checked) }
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(52.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(IrisSurfaceBright),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = IrisTextSoft)
            if (coverUri != null) {
                AsyncImage(
                    model = Uri.parse(coverUri),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(text = summary, fontSize = 12.sp, color = IrisTextSoft, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (expandable) {
            IconButton(onClick = onExpand) {
                Icon(if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore, contentDescription = null)
            }
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

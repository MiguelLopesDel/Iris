package com.iris.app.ui.screens.sync

import com.iris.app.data.model.DeviceMediaSource
import java.util.Locale

/**
 * The phone's media folders as a person thinks of them, for choosing what to
 * back up: "Câmera", "WhatsApp", "Capturas de tela", not `DCIM/Camera/` and
 * `Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/Sent/`.
 *
 * MediaStore reports one source per folder and media kind, and a phone easily
 * holds hundreds of folders. Here a folder's photos and videos are one entry,
 * the folders of a known app (WhatsApp's images, videos, GIFs, sent ones) are
 * one group, and everything else is kept apart from the main choices.
 */

/** Apps and system places people recognise, in the order they are shown. */
enum class FolderApp(val title: String) {
    CAMERA("Câmera"),
    SCREENSHOTS("Capturas de tela"),
    SCREEN_RECORDINGS("Gravações de tela"),
    WHATSAPP("WhatsApp"),
    TELEGRAM("Telegram"),
    INSTAGRAM("Instagram"),
    DISCORD("Discord"),
    FACEBOOK("Facebook"),
    TIKTOK("TikTok"),
    SNAPCHAT("Snapchat"),
    TWITTER("X (Twitter)"),
    DOWNLOADS("Downloads"),
}

/** One folder on the phone, photos and videos together. */
data class BackupFolder(
    val key: String,
    val title: String,
    val app: FolderApp?,
    val sourceIds: List<String>,
    val photos: Int,
    val videos: Int,
    val coverUri: String?,
) {
    val items: Int get() = photos + videos
}

/** What one switch in the chooser stands for: a known app's folders, or one folder. */
data class BackupFolderGroup(
    val key: String,
    val title: String,
    val app: FolderApp?,
    val folders: List<BackupFolder>,
) {
    val sourceIds: List<String> get() = folders.flatMap { it.sourceIds }
    val photos: Int get() = folders.sumOf { it.photos }
    val videos: Int get() = folders.sumOf { it.videos }
    val items: Int get() = photos + videos
    val coverUri: String? get() = folders.maxByOrNull { it.items }?.coverUri

    fun selection(selectedIds: Set<String>): GroupSelection {
        val chosen = sourceIds.count { it in selectedIds }
        return when (chosen) {
            0 -> GroupSelection.NONE
            sourceIds.size -> GroupSelection.ALL
            else -> GroupSelection.SOME
        }
    }
}

enum class GroupSelection { NONE, SOME, ALL }

/** The chooser's two lists: the places people recognise, and the rest, biggest first. */
data class BackupFolderSections(
    val main: List<BackupFolderGroup>,
    val others: List<BackupFolderGroup>,
)

object BackupFolders {
    fun sections(sources: List<DeviceMediaSource>, query: String = ""): BackupFolderSections {
        val groups = groups(folders(sources))
        val normalized = query.trim().lowercase(Locale.ROOT)
        val matching = if (normalized.isEmpty()) groups else groups.filter { group ->
            group.title.lowercase(Locale.ROOT).contains(normalized) ||
                group.folders.any { it.title.lowercase(Locale.ROOT).contains(normalized) }
        }
        val (main, others) = matching.partition { it.app != null }
        return BackupFolderSections(
            main = main.sortedBy { it.app!!.ordinal },
            others = others.sortedByDescending { it.items },
        )
    }

    /** One entry per folder: its photo and video sources merged. */
    fun folders(sources: List<DeviceMediaSource>): List<BackupFolder> =
        sources.groupBy { folderKey(it) }.map { (key, parts) ->
            val first = parts.first()
            val app = appOf(first.relativePath, first.name)
            BackupFolder(
                key = key,
                title = folderTitle(app, first.relativePath, first.name),
                app = app,
                sourceIds = parts.map { it.id },
                photos = parts.filter { it.mediaKind != "video" }.sumOf { it.itemCount },
                videos = parts.filter { it.mediaKind == "video" }.sumOf { it.itemCount },
                coverUri = parts.firstNotNullOfOrNull { it.coverUri },
            )
        }

    private fun groups(folders: List<BackupFolder>): List<BackupFolderGroup> {
        val (known, rest) = folders.partition { it.app != null }
        val byApp = known.groupBy { it.app!! }.map { (app, members) ->
            BackupFolderGroup(
                key = "app:${app.name}",
                title = app.title,
                app = app,
                folders = members.sortedByDescending { it.items },
            )
        }
        val single = rest.map { BackupFolderGroup(key = it.key, title = it.title, app = null, folders = listOf(it)) }
        return byApp + single
    }

    /** `volume:bucket`, the source id without its media kind. */
    private fun folderKey(source: DeviceMediaSource): String = source.id.substringBeforeLast(':')

    fun appOf(relativePath: String, name: String): FolderApp? {
        val path = "${relativePath.lowercase(Locale.ROOT)}/${name.lowercase(Locale.ROOT)}"
        return when {
            "screenrecord" in path || "screen record" in path || "gravações de tela" in path -> FolderApp.SCREEN_RECORDINGS
            "screenshot" in path || "capturas de tela" in path -> FolderApp.SCREENSHOTS
            "whatsapp" in path -> FolderApp.WHATSAPP
            "telegram" in path -> FolderApp.TELEGRAM
            "instagram" in path -> FolderApp.INSTAGRAM
            "discord" in path -> FolderApp.DISCORD
            "facebook" in path || "messenger" in path -> FolderApp.FACEBOOK
            "tiktok" in path || "musically" in path -> FolderApp.TIKTOK
            "snapchat" in path -> FolderApp.SNAPCHAT
            "twitter" in path -> FolderApp.TWITTER
            path.startsWith("dcim/camera") || name.equals("Camera", ignoreCase = true) -> FolderApp.CAMERA
            path.startsWith("download") -> FolderApp.DOWNLOADS
            else -> null
        }
    }

    /** A folder's own name in a group: "Imagens", "Enviadas"; outside one, its name as the phone shows it. */
    fun folderTitle(app: FolderApp?, relativePath: String, name: String): String {
        val lower = name.lowercase(Locale.ROOT)
        val sent = relativePath.trimEnd('/').substringAfterLast('/').equals("Sent", ignoreCase = true)
        return when (app) {
            FolderApp.WHATSAPP -> when {
                sent -> "Enviadas"
                "images" in lower -> "Imagens"
                "video" in lower -> "Vídeos"
                "gif" in lower -> "GIFs"
                "sticker" in lower -> "Figurinhas"
                "status" in lower -> "Status"
                else -> name
            }
            FolderApp.CAMERA -> "Câmera"
            FolderApp.SCREENSHOTS -> "Capturas de tela"
            FolderApp.SCREEN_RECORDINGS -> "Gravações de tela"
            FolderApp.DOWNLOADS -> if (lower == "download" || lower == "downloads") "Downloads" else name
            else -> if (sent) "Enviadas" else name
        }
    }
}

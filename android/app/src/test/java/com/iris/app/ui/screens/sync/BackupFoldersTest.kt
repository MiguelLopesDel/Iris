package com.iris.app.ui.screens.sync

import com.iris.app.data.model.DeviceMediaSource
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BackupFoldersTest {
    private fun source(bucket: String, name: String, path: String, kind: String = "image", count: Int = 10) =
        DeviceMediaSource(
            id = "external_primary:$bucket:$kind",
            name = name,
            relativePath = path,
            volume = "external_primary",
            mediaKind = kind,
            itemCount = count,
            coverUri = "content://media/$bucket/$kind",
        )

    private val phone = listOf(
        source("1", "Camera", "DCIM/Camera/", count = 900),
        source("1", "Camera", "DCIM/Camera/", kind = "video", count = 40),
        source("2", "Screenshots", "Pictures/Screenshots/", count = 300),
        source("3", "WhatsApp Images", "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/", count = 500),
        source("4", "Sent", "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/Sent/", count = 50),
        source("5", "WhatsApp Video", "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Video/", kind = "video", count = 80),
        source("6", "Reddit", "Pictures/Reddit/", count = 7),
        source("7", "Memes", "Pictures/Memes/", count = 120),
        source("8", "Download", "Download/", count = 15),
    )

    @Test
    fun `a folder's photos and videos are one entry`() {
        val camera = BackupFolders.folders(phone).single { it.key == "external_primary:1" }
        assertEquals("Câmera", camera.title)
        assertEquals(900, camera.photos)
        assertEquals(40, camera.videos)
        assertEquals(listOf("external_primary:1:image", "external_primary:1:video"), camera.sourceIds)
    }

    @Test
    fun `known places come first in a fixed order, the rest biggest first`() {
        val sections = BackupFolders.sections(phone)
        assertEquals(listOf("Câmera", "Capturas de tela", "WhatsApp", "Downloads"), sections.main.map { it.title })
        assertEquals(listOf("Memes", "Reddit"), sections.others.map { it.title })
    }

    @Test
    fun `an app's folders are one group with readable names`() {
        val whatsapp = BackupFolders.sections(phone).main.single { it.app == FolderApp.WHATSAPP }
        assertEquals(listOf("Imagens", "Vídeos", "Enviadas"), whatsapp.folders.map { it.title })
        assertEquals(630, whatsapp.items)
    }

    @Test
    fun `a group's switch is on, off or partly on`() {
        val whatsapp = BackupFolders.sections(phone).main.single { it.app == FolderApp.WHATSAPP }
        assertEquals(GroupSelection.NONE, whatsapp.selection(emptySet()))
        assertEquals(GroupSelection.SOME, whatsapp.selection(setOf("external_primary:3:image")))
        assertEquals(GroupSelection.ALL, whatsapp.selection(whatsapp.sourceIds.toSet()))
    }

    @Test
    fun `search finds a group by one of its folders`() {
        val sections = BackupFolders.sections(phone, query = "enviadas")
        assertEquals(listOf("WhatsApp"), sections.main.map { it.title })
        assertEquals(emptyList<String>(), sections.others.map { it.title })
    }

    @Test
    fun `an unknown folder keeps the name the phone gives it`() {
        assertNull(BackupFolders.appOf("Pictures/Memes/", "Memes"))
        assertEquals("Memes", BackupFolders.folderTitle(null, "Pictures/Memes/", "Memes"))
    }
}

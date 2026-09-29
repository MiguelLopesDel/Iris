package com.iris.app

import com.iris.app.data.model.AddSpaceItemResponse
import com.iris.app.data.model.SaveSpaceItemResponse
import com.iris.app.data.model.SpaceAlbumsResponse
import com.iris.app.data.model.SpaceItemsResponse
import com.iris.app.data.model.SpaceMembersResponse
import com.iris.app.data.model.SpaceResponse
import com.iris.app.data.model.SpaceSearchResponse
import com.iris.app.data.model.SpacesResponse
import com.iris.app.data.remote.IrisApiClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Decodes shared-space responses written by the real server in private mode
 * (`tests/test_android_space_fixtures.py`). The fixtures show a viewer's view,
 * the case where the app must not offer adding or removing.
 */
class SpaceContractTest {

    private val json = IrisApiClient.RESPONSE_JSON

    private fun fixture(name: String): String =
        checkNotNull(javaClass.classLoader?.getResourceAsStream("fixtures/$name")) {
            "Fixture ausente: fixtures/$name — rode tests/test_android_space_fixtures.py."
        }.bufferedReader().readText()

    @Test
    fun `a viewer lists the space with a role that cannot add`() {
        val spaces = json.decodeFromString<SpacesResponse>(fixture("spaces.json")).spaces
        assertEquals(listOf("Família"), spaces.map { it.name })
        assertEquals("viewer", spaces.single().role)
        assertEquals("Visualizador", spaces.single().roleLabel)
        assertFalse(spaces.single().canAdd)

        val detail = json.decodeFromString<SpaceResponse>(fixture("space_detail.json")).space
        assertEquals(spaces.single().id, detail.id)
    }

    @Test
    fun `a manager creating a space can add to it`() {
        val created = json.decodeFromString<SpaceResponse>(fixture("space_created.json")).space
        assertEquals("manager", created.role)
        assertTrue(created.canAdd)
    }

    @Test
    fun `items page decodes with relative urls and no remove action for a viewer`() {
        val page = json.decodeFromString<SpaceItemsResponse>(fixture("space_items.json"))
        val item = page.items.single()
        assertEquals("ana.jpg", item.name)
        assertEquals("ana", item.addedByUsername)
        assertFalse(item.isVideo)
        assertFalse(item.canRemove)
        assertTrue(item.thumbnailUrl!!.startsWith("/api/spaces/"))
        assertTrue(item.originalUrl!!.endsWith("/original"))
        assertNull(page.nextBefore)
    }

    @Test
    fun `adding and saving answer in shapes the app understands`() {
        val added = json.decodeFromString<AddSpaceItemResponse>(fixture("space_item_added.json"))
        assertTrue(added.created)
        assertTrue(added.item.canRemove) // the author sees their own item as removable

        val saved = json.decodeFromString<SaveSpaceItemResponse>(fixture("space_item_saved.json"))
        assertEquals("pending_processing", saved.state)
    }

    @Test
    fun `members mark which one is you`() {
        val members = json.decodeFromString<SpaceMembersResponse>(fixture("space_members.json")).members
        assertEquals(listOf("ana", "bruno"), members.map { it.username })
        assertEquals("bruno", members.single { it.isYou }.username)
    }

    @Test
    fun `albums decode with a cover and their photos page`() {
        val album = json.decodeFromString<SpaceAlbumsResponse>(fixture("space_albums.json")).albums.single()
        assertEquals("Praia", album.name)
        assertEquals(1, album.count)
        assertTrue(album.coverUrl!!.endsWith("/thumbnail"))

        val page = json.decodeFromString<SpaceItemsResponse>(fixture("space_album_items.json"))
        assertEquals(listOf("ana.jpg"), page.items.map { it.name })
    }

    @Test
    fun `search results decode and say whether meaning was used`() {
        val found = json.decodeFromString<SpaceSearchResponse>(fixture("space_search.json"))
        assertEquals(listOf("ana.jpg"), found.items.map { it.name })
        assertFalse(found.semantic)
    }
}

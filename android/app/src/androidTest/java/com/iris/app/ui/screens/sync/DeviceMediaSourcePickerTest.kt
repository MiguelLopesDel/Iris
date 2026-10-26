package com.iris.app.ui.screens.sync

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.iris.app.R
import com.iris.app.data.model.DeviceMediaSource
import com.iris.app.ui.theme.IrisTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DeviceMediaSourcePickerTest {

    /** The app's own wording in the device's language, so the test runs in any locale. */
    private fun text(id: Int): String =
        InstrumentationRegistry.getInstrumentation().targetContext.getString(id)
    @get:Rule
    val compose = createAndroidComposeRule<PickerTestActivity>()

    private val sources = listOf(
        DeviceMediaSource("camera", "Camera", "DCIM/Camera/", "external", "image", 50),
        DeviceMediaSource("whatsapp", "WhatsApp Images", "Pictures/WhatsApp/", "external", "image", 12),
        DeviceMediaSource("movies", "Movies", "Movies/", "external", "video", 3),
    )

    @Test
    fun mediaFiltersSearchAndSelectedFilterUseVisibleSemantics() {
        compose.setContent {
            var selectedIds by remember { mutableStateOf(setOf("whatsapp")) }
            IrisTheme {
                DeviceMediaSourcePicker(
                    sources = sources,
                    selectedIds = selectedIds,
                    isLoading = false,
                    hasLimitedMediaAccess = false,
                    errorMessage = null,
                    onToggleSource = { id, selected ->
                        selectedIds = if (selected) selectedIds + id else selectedIds - id
                    },
                    onClearSelection = { selectedIds = emptySet() },
                    onRefresh = {},
                    onRequestMediaAccess = {},
                    onDismiss = {},
                )
            }
        }

        compose.onNodeWithText("Camera").assertIsDisplayed()
        compose.onNodeWithText("Movies").assertIsDisplayed()

        compose.onNodeWithText(text(R.string.sync_source_filter_videos)).performClick()
        compose.onNodeWithText("Movies").assertIsDisplayed()
        assertTrue(compose.onAllNodesWithText("Camera").fetchSemanticsNodes().isEmpty())

        compose.onNodeWithText(text(R.string.sync_source_filter_photos)).performClick()
        compose.onNodeWithText("WhatsApp Images").assertIsDisplayed()
        assertTrue(compose.onAllNodesWithText("Movies").fetchSemanticsNodes().isEmpty())

        compose.onNode(hasSetTextAction()).performTextInput("whatsapp")
        compose.onNodeWithText("WhatsApp Images").assertIsDisplayed()
        assertTrue(compose.onAllNodesWithText("Camera").fetchSemanticsNodes().isEmpty())

        compose.onNodeWithContentDescription(text(R.string.sync_source_picker_clear_search)).performClick()
        compose.onNodeWithText(text(R.string.sync_source_filter_selected)).performClick()
        compose.onNodeWithText("WhatsApp Images").assertIsDisplayed()
        assertTrue(compose.onAllNodesWithText("Camera").fetchSemanticsNodes().isEmpty())

        compose.onNodeWithText(text(R.string.sync_source_picker_clear)).performClick()
        compose.onNodeWithText(text(R.string.sync_source_picker_no_results)).assertIsDisplayed()
    }

    @Test
    fun loadingEmptyAndErrorStatesAreVisibleThroughSemantics() {
        var completeDiscovery: () -> Unit = {}
        var failDiscovery: () -> Unit = {}
        compose.setContent {
            var loading by remember { mutableStateOf(true) }
            var error by remember { mutableStateOf<String?>(null) }
            completeDiscovery = { loading = false }
            failDiscovery = {
                loading = false
                error = "Acesso às mídias negado"
            }
            IrisTheme {
                DeviceMediaSourcePicker(
                    sources = emptyList(),
                    selectedIds = emptySet(),
                    isLoading = loading,
                    hasLimitedMediaAccess = false,
                    errorMessage = error,
                    onToggleSource = { _, _ -> },
                    onClearSelection = {},
                    onRefresh = { loading = true; error = null },
                    onRequestMediaAccess = {},
                    onDismiss = {},
                )
            }
        }

        compose.onNodeWithText(text(R.string.sync_source_picker_loading)).assertIsDisplayed()
        compose.runOnIdle { completeDiscovery() }
        compose.onNodeWithText(text(R.string.sync_source_picker_no_sources)).assertIsDisplayed()

        compose.runOnIdle { failDiscovery() }
        compose.onNodeWithText("Acesso às mídias negado").assertIsDisplayed()
    }

    @Test
    fun selectingARowAndCompletingUsesItsSemanticActions() {
        val toggles = mutableListOf<Pair<String, Boolean>>()
        var dismissals = 0
        compose.setContent {
            IrisTheme {
                DeviceMediaSourcePicker(
                    sources = sources,
                    selectedIds = emptySet(),
                    isLoading = false,
                    hasLimitedMediaAccess = false,
                    errorMessage = null,
                    onToggleSource = { id, selected -> toggles += id to selected },
                    onClearSelection = {},
                    onRefresh = {},
                    onRequestMediaAccess = {},
                    onDismiss = { dismissals++ },
                )
            }
        }

        compose.onNodeWithTag("device-media-source-camera").performClick()
        compose.runOnIdle { assertEquals(listOf("camera" to true), toggles) }

        compose.onNodeWithText(text(R.string.sync_source_picker_done)).performClick()
        compose.runOnIdle { assertEquals(1, dismissals) }
    }
}

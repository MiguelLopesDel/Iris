package com.iris.app

import com.iris.app.data.model.MediaPersonRef
import com.iris.app.data.model.MediaRecord
import com.iris.app.data.model.Person
import com.iris.app.data.model.RecordsResponse
import com.iris.app.data.model.SearchResponse
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.remote.IrisApiClient
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class IrisClientTest {

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
    }

    @Test
    fun testBaseUrlNormalization() {
        assertEquals("http://192.168.1.100:8000/", IrisApiClient.normalizeBaseUrl("192.168.1.100:8000"))
        assertEquals("http://192.168.1.100:8000/", IrisApiClient.normalizeBaseUrl("http://192.168.1.100:8000"))
        assertEquals("https://iris.example.com/", IrisApiClient.normalizeBaseUrl("https://iris.example.com"))
        assertEquals("https://iris.example.com/", IrisApiClient.normalizeBaseUrl("https://iris.example.com/"))
    }

    @Test
    fun testMediaUrlResolution() {
        val client = IrisApiClient("http://10.0.2.2:8000/")
        val resolved = client.resolveMediaUrl("/media/memes/cat photo.jpg")
        assertEquals("http://10.0.2.2:8000/media/media/memes/cat%20photo.jpg", resolved)

        val thumbUrl = client.resolveThumbnailUrl("/thumbs/abc12345.jpg")
        assertEquals("http://10.0.2.2:8000/thumbs/abc12345.jpg", thumbUrl)

        val faceThumbUrl = client.resolveFaceThumbnailUrl(42)
        assertEquals("http://10.0.2.2:8000/api/faces/42/thumb", faceThumbUrl)
    }

    @Test
    fun testMediaRecordProperties() {
        val videoRecord = MediaRecord(
            index = 1,
            arquivo = "meme_video.mp4",
            mediaType = "video",
            tags = "funny, cat, zoom"
        )
        assertTrue(videoRecord.isVideo)
        assertEquals("meme_video.mp4", videoRecord.cleanFilename)
        assertEquals(listOf("funny", "cat", "zoom"), videoRecord.tagsList)

        val imageRecord = MediaRecord(
            index = 2,
            arquivo = "",
            resolvedPath = "/var/memes/photo.png",
            mediaType = "image"
        )
        assertFalse(imageRecord.isVideo)
        assertEquals("photo.png", imageRecord.cleanFilename)
    }

    @Test
    fun testServerInfoDeserialization() {
        val jsonString = """
            {
                "records": 1250,
                "model": "ViT-L-14",
                "device": "cuda:0",
                "db": "iris_v1.db",
                "faiss_index_exists": true,
                "florence_model": "microsoft/Florence-2-large"
            }
        """.trimIndent()

        val info = json.decodeFromString<ServerInfo>(jsonString)
        assertEquals(1250, info.records)
        assertEquals("cuda:0", info.device)
        assertEquals("ViT-L-14", info.model)
        assertTrue(info.faissIndexExists)
    }

    @Test
    fun testRecordsResponseDeserialization() {
        val jsonString = """
            {
                "page": 1,
                "per_page": 24,
                "total": 1,
                "total_pages": 1,
                "records": [
                    {
                        "index": 10,
                        "arquivo": "surprised_pikachu.jpg",
                        "media_type": "image",
                        "texto_extraido": "pika pika",
                        "descricao_ia": "A yellow pokemon looking surprised",
                        "persons": [
                            { "id": 1, "name": "Ash Ketchum", "face_id": 5 }
                        ]
                    }
                ]
            }
        """.trimIndent()

        val response = json.decodeFromString<RecordsResponse>(jsonString)
        assertEquals(1, response.total)
        assertEquals(1, response.records.size)
        val record = response.records[0]
        assertEquals("surprised_pikachu.jpg", record.arquivo)
        assertEquals(1, record.persons.size)
        assertEquals("Ash Ketchum", record.persons[0].name)
    }

    @Test
    fun testSearchResponseDeserialization() {
        val jsonString = """
            {
                "query": "gato rindo",
                "total": 1,
                "results": [
                    {
                        "index": 7,
                        "arquivo": "laughing_cat.mp4",
                        "media_type": "video",
                        "score": 0.895
                    }
                ]
            }
        """.trimIndent()

        val response = json.decodeFromString<SearchResponse>(jsonString)
        assertEquals(1, response.total)
        assertEquals("gato rindo", response.query)
        assertEquals(0.895f, response.results[0].score)
        assertTrue(response.results[0].isVideo)
    }

    @Test
    fun testDeviceLoginResponseDeserialization() {
        val jsonString = """
            {
                "user": {
                    "id": 1,
                    "username": "alice",
                    "display_name": "Alice",
                    "is_admin": true
                },
                "access_token": "mock_access_token_123",
                "refresh_token": "mock_refresh_token_456",
                "token_type": "Bearer",
                "expires_in": 900,
                "device_id": "dev_789"
            }
        """.trimIndent()

        val response = json.decodeFromString<com.iris.app.data.model.DeviceLoginResponse>(jsonString)
        assertEquals("alice", response.user?.username)
        assertEquals("mock_access_token_123", response.accessToken)
        assertEquals("mock_refresh_token_456", response.refreshToken)
        assertEquals("dev_789", response.deviceId)
        assertEquals(900L, response.expiresIn)
    }

    @Test
    fun testUploadLifecycleResponses() {
        val initJson = """
            {
                "upload_id": "up_001",
                "offset": 0,
                "chunk_size": 33554432
            }
        """.trimIndent()
        val initResp = json.decodeFromString<com.iris.app.data.model.UploadInitResponse>(initJson)
        assertEquals("up_001", initResp.uploadId)
        assertEquals(0L, initResp.offset)
        assertEquals(33554432, initResp.chunkSize)

        val statusJson = """
            {
                "upload_id": "up_001",
                "offset": 16777216,
                "size": 33554432,
                "state": "uploading"
            }
        """.trimIndent()
        val statusResp = json.decodeFromString<com.iris.app.data.model.UploadStatusResponse>(statusJson)
        assertEquals(16777216L, statusResp.offset)
        assertEquals("uploading", statusResp.state)

        val completePendingJson = """
            {
                "upload_id": "up_001",
                "state": "pending_processing",
                "cursor": 42,
                "path": "/data/uploads/2026-09/photo.jpg"
            }
        """.trimIndent()
        val completeResp = json.decodeFromString<com.iris.app.data.model.UploadCompleteResponse>(completePendingJson)
        assertEquals("pending_processing", completeResp.state)
        assertEquals(42L, completeResp.cursor)

        val duplicateJson = """
            {
                "upload_id": "up_001",
                "state": "duplicate",
                "media_id": 99,
                "cursor": 43
            }
        """.trimIndent()
        val duplicateResp = json.decodeFromString<com.iris.app.data.model.UploadCompleteResponse>(duplicateJson)
        assertEquals("duplicate", duplicateResp.state)
        assertEquals(99, duplicateResp.mediaId)
    }

    @Test
    fun testChangesFeedDeserialization() {
        val changesJson = """
            {
                "changes": [
                    {
                        "cursor": 1,
                        "entity_type": "media",
                        "entity_id": "up_001",
                        "operation": "created",
                        "version": 1,
                        "payload": {
                            "upload_id": "up_001",
                            "state": "pending_processing"
                        }
                    },
                    {
                        "cursor": 2,
                        "entity_type": "media",
                        "entity_id": "up_001",
                        "operation": "updated",
                        "version": 2,
                        "payload": {
                            "upload_id": "up_001",
                            "state": "ready"
                        }
                    }
                ],
                "next_cursor": 2,
                "has_more": false
            }
        """.trimIndent()

        val changesResp = json.decodeFromString<com.iris.app.data.model.ChangesResponse>(changesJson)
        assertEquals(2, changesResp.changes.size)
        assertEquals(2L, changesResp.nextCursor)
        assertFalse(changesResp.hasMore)
        assertEquals("pending_processing", changesResp.changes[0].payload?.get("state")?.toString()?.replace("\"", ""))
        assertEquals("ready", changesResp.changes[1].payload?.get("state")?.toString()?.replace("\"", ""))
    }

    @Test
    fun testAllRequiredUploadStatesExist() {
        val states = com.iris.app.data.model.UploadJobState.values().map { it.name }
        assertTrue(states.contains("QUEUED"))
        assertTrue(states.contains("UPLOADING"))
        assertTrue(states.contains("PENDING_PROCESSING"))
        assertTrue(states.contains("PROCESSING"))
        assertTrue(states.contains("READY"))
        assertTrue(states.contains("DUPLICATE"))
        assertTrue(states.contains("FAILED"))
        assertTrue(states.contains("FAILED_PROCESSING"))
    }
}

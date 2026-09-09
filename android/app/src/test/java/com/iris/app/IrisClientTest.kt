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
}

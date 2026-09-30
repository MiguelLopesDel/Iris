package com.iris.app.data.sync

import com.iris.app.data.remote.IrisApiService
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okio.BufferedSink
import kotlin.random.Random

/**
 * Measures the path from this device to the Iris server with synthetic bytes
 * the server never keeps. Comparing its phases with the sync rate tells where
 * time goes: network and HTTP alone, parallel connections, or the server's
 * disk writes that every uploaded chunk also pays.
 */
class ServerSpeedTest(
    private val bytesPerPhase: Long = DEFAULT_BYTES_PER_PHASE,
    private val parallelConnections: Int = DEFAULT_PARALLEL_CONNECTIONS,
    private val nowNanos: () -> Long = System::nanoTime,
) {
    enum class Phase { SINGLE_CONNECTION, PARALLEL_CONNECTIONS, SERVER_DISK }

    data class Result(val phase: Phase, val bytes: Long, val elapsedMillis: Long) {
        val bytesPerSecond: Double
            get() = if (elapsedMillis > 0L) bytes * 1_000.0 / elapsedMillis else 0.0
    }

    suspend fun run(api: IrisApiService, onResult: (Result) -> Unit = {}): List<Result> {
        val results = mutableListOf<Result>()
        for (phase in Phase.entries) {
            val result = measure(api, phase)
            results += result
            onResult(result)
        }
        return results
    }

    private suspend fun measure(api: IrisApiService, phase: Phase): Result = coroutineScope {
        val connections = if (phase == Phase.PARALLEL_CONNECTIONS) parallelConnections else 1
        val mode = if (phase == Phase.SERVER_DISK) "disk" else "discard"
        val perConnection = bytesPerPhase / connections
        val started = nowNanos()
        val received = List(connections) {
            async { api.speedTest(mode, syntheticBody(perConnection)).bytes }
        }.awaitAll().sum()
        Result(phase, received, (nowNanos() - started) / 1_000_000)
    }

    companion object {
        const val DEFAULT_BYTES_PER_PHASE = 64L * 1024 * 1024
        const val DEFAULT_PARALLEL_CONNECTIONS = 4
        private const val BLOCK_BYTES = 64 * 1024

        // Random rather than zeros, so no layer on the path can compress it.
        private val block: ByteArray = Random(20260930).nextBytes(BLOCK_BYTES)

        internal fun syntheticBody(length: Long): RequestBody = object : RequestBody() {
            override fun contentType() = "application/octet-stream".toMediaType()
            override fun contentLength(): Long = length

            override fun writeTo(sink: BufferedSink) {
                var remaining = length
                while (remaining > 0L) {
                    val count = minOf(remaining, BLOCK_BYTES.toLong()).toInt()
                    sink.write(block, 0, count)
                    remaining -= count
                }
            }
        }
    }
}

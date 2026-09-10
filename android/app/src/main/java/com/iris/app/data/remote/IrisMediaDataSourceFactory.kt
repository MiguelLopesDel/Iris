package com.iris.app.data.remote

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.HttpUtil
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.io.EOFException
import java.io.IOException
import java.io.InputStream

/**
 * Factory creating [IrisOkHttpDataSource] instances backed by the authenticated
 * OkHttpClient instance, which automatically injects Bearer credentials and handles
 * token refreshing on 401s via [TokenAuthenticator].
 */
class IrisMediaDataSourceFactory(
    private val okHttpClient: OkHttpClient
) : DataSource.Factory {
    override fun createDataSource(): DataSource {
        return IrisOkHttpDataSource(okHttpClient)
    }
}

/**
 * Custom Media3 [DataSource] backed by [OkHttpClient].
 */
class IrisOkHttpDataSource(
    private val client: OkHttpClient
) : BaseDataSource(/* isNetwork = */ true) {

    private var dataSpec: DataSpec? = null
    private var response: Response? = null
    private var responseByteStream: InputStream? = null
    private var opened = false
    private var bytesToRead: Long = 0
    private var bytesRead: Long = 0

    override fun open(dataSpec: DataSpec): Long {
        this.dataSpec = dataSpec
        this.bytesRead = 0
        this.bytesToRead = 0
        transferInitializing(dataSpec)

        val requestBuilder = Request.Builder().url(dataSpec.uri.toString())
        for ((key, value) in dataSpec.httpRequestHeaders) {
            requestBuilder.header(key, value)
        }

        if (dataSpec.position != 0L || dataSpec.length != C.LENGTH_UNSET.toLong()) {
            val rangeHeader = HttpUtil.buildRangeRequestHeader(dataSpec.position, dataSpec.length)
            if (rangeHeader != null) {
                requestBuilder.addHeader("Range", rangeHeader)
            }
        }

        val request = requestBuilder.build()
        val call = client.newCall(request)
        val resp: Response
        try {
            resp = call.execute()
            this.response = resp
        } catch (e: IOException) {
            throw HttpDataSource.HttpDataSourceException.createForIOException(
                e,
                dataSpec,
                HttpDataSource.HttpDataSourceException.TYPE_OPEN
            )
        }

        val responseCode = resp.code
        if (!resp.isSuccessful) {
            val headers = resp.headers.toMultimap()
            close()
            throw HttpDataSource.InvalidResponseCodeException(
                responseCode,
                resp.message,
                null,
                headers,
                dataSpec,
                ByteArray(0)
            )
        }

        val responseBody = resp.body
            ?: throw HttpDataSource.HttpDataSourceException(
                "Null response body",
                dataSpec,
                HttpDataSource.HttpDataSourceException.TYPE_OPEN
            )
        this.responseByteStream = responseBody.byteStream()

        val contentLength = HttpUtil.getContentLength(
            resp.header("Content-Range"),
            resp.header("Content-Length")
        )
        bytesToRead = if (dataSpec.length != C.LENGTH_UNSET.toLong()) {
            dataSpec.length
        } else if (contentLength != C.LENGTH_UNSET.toLong()) {
            contentLength
        } else {
            C.LENGTH_UNSET.toLong()
        }

        opened = true
        transferStarted(dataSpec)
        return bytesToRead
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        val stream = responseByteStream ?: return C.RESULT_END_OF_INPUT

        val bytesToReadNow = if (bytesToRead != C.LENGTH_UNSET.toLong()) {
            val remaining = bytesToRead - bytesRead
            if (remaining <= 0) return C.RESULT_END_OF_INPUT
            minOf(length.toLong(), remaining).toInt()
        } else {
            length
        }

        val read = try {
            stream.read(buffer, offset, bytesToReadNow)
        } catch (e: IOException) {
            throw HttpDataSource.HttpDataSourceException.createForIOException(
                e,
                dataSpec ?: DataSpec(Uri.EMPTY),
                HttpDataSource.HttpDataSourceException.TYPE_READ
            )
        }

        if (read == -1) {
            if (bytesToRead != C.LENGTH_UNSET.toLong() && bytesToRead != bytesRead) {
                throw HttpDataSource.HttpDataSourceException(
                    EOFException(),
                    dataSpec ?: DataSpec(Uri.EMPTY),
                    HttpDataSource.HttpDataSourceException.TYPE_READ
                )
            }
            return C.RESULT_END_OF_INPUT
        }

        bytesRead += read
        bytesTransferred(read)
        return read
    }

    override fun getUri(): Uri? {
        val url = response?.request?.url?.toString()
        return if (url != null) Uri.parse(url) else dataSpec?.uri
    }

    override fun getResponseHeaders(): Map<String, List<String>> {
        return response?.headers?.toMultimap() ?: emptyMap()
    }

    override fun close() {
        if (opened) {
            opened = false
            try {
                responseByteStream?.close()
            } catch (_: IOException) {}
            response?.close()
            responseByteStream = null
            response = null
            transferEnded()
        }
    }
}

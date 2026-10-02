package com.iris.app

import com.iris.app.data.remote.security.ServerOrigin
import okhttp3.mockwebserver.MockWebServer

/**
 * Mock servers speak plain HTTP, which the app refuses for an origin the user
 * never allowed. Tests grant it for the mock server's origin only, and revoke
 * it afterwards so the stored policy does not outlive the test.
 */
internal fun IrisApplication.allowCleartext(server: MockWebServer, allowed: Boolean = true) {
    apiClient.connectionSecurity.update(ServerOrigin.of(server.url("/"))) { it.copy(cleartextAllowed = allowed) }
}

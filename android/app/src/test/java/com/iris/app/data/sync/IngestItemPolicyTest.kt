package com.iris.app.data.sync

import com.iris.app.data.sync.IngestItemPolicy.Action
import org.junit.Assert.assertEquals
import org.junit.Test

class IngestItemPolicyTest {
    @Test
    fun settledStatesConfirmThePhoto() {
        assertEquals(Action.READY, IngestItemPolicy.ofState("ready"))
        assertEquals(Action.DUPLICATE, IngestItemPolicy.ofState("duplicate"))
        assertEquals(Action.PENDING, IngestItemPolicy.ofState("pending_processing"))
        assertEquals(Action.PENDING, IngestItemPolicy.ofState("processing"))
    }

    @Test
    fun aPhotoReservedButNotReceivedIsSentAgain() {
        assertEquals(Action.SEND_AGAIN, IngestItemPolicy.ofState("uploading"))
        assertEquals(Action.SEND_AGAIN, IngestItemPolicy.ofState("receiving"))
    }

    @Test
    fun errorsFollowTheResumablePathsRules() {
        assertEquals(Action.HASH_AGAIN, IngestItemPolicy.ofError(422))
        assertEquals(Action.RESERVE_AGAIN, IngestItemPolicy.ofError(404))
        for (code in listOf(401, 409, 408, 429, 500, 503)) assertEquals(Action.SEND_AGAIN, IngestItemPolicy.ofError(code))
        for (code in listOf(400, 403, 413)) assertEquals(Action.FAIL, IngestItemPolicy.ofError(code))
        assertEquals(Action.FAIL, IngestItemPolicy.ofState("failed_processing"))
    }
}

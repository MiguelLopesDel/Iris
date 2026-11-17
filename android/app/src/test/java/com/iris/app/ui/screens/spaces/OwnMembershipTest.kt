package com.iris.app.ui.screens.spaces

import com.iris.app.data.model.SpaceMember
import com.iris.app.data.model.ownMembership
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OwnMembershipTest {

    @Test
    fun `finds this account among the members`() {
        val members = listOf(SpaceMember(userId = 1, isYou = false), SpaceMember(userId = 2, isYou = true))

        assertEquals(2, ownMembership(members)?.userId)
    }

    @Test
    fun `an account already removed has nothing to leave instead of an error`() {
        assertNull(ownMembership(listOf(SpaceMember(userId = 1, isYou = false))))
    }
}

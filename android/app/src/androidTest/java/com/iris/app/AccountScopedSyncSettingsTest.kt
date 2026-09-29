package com.iris.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AccountScopedSyncSettingsTest {

    @Test
    fun sync_preferences_are_isolated_per_account_and_new_accounts_get_defaults() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val app = context.applicationContext as IrisApplication
        val repository = app.settingsRepository
        val unique = System.nanoTime()
        val accountA = "settings-test-$unique|user:1"
        val accountB = "settings-test-$unique|user:2"

        assertFalse(repository.syncSettingsForAccount(accountA).first().autoBackupEnabled)
        assertFalse(repository.syncSettingsForAccount(accountA).first().backupSetupPromptAnswered)
        repository.updateAutoBackupEnabled(accountA, true)
        repository.updateSelectedSourceIds(accountA, setOf("camera"))

        val settingsB = repository.syncSettingsForAccount(accountB).first()
        assertFalse("A's opt-in must not enable backups for B", settingsB.autoBackupEnabled)
        assertTrue("New accounts start with no selected folders", settingsB.selectedSourceIds.isEmpty())

        val settingsA = repository.syncSettingsForAccount(accountA).first()
        assertTrue(settingsA.autoBackupEnabled)
        assertEquals(setOf("camera"), settingsA.selectedSourceIds)
    }

    @Test
    fun first_use_backup_choice_is_account_scoped_and_folder_opt_in_starts_backup() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val app = context.applicationContext as IrisApplication
        val repository = app.settingsRepository
        val unique = System.nanoTime()
        val accountA = "backup-onboarding-$unique|user:1"
        val accountB = "backup-onboarding-$unique|user:2"

        val newAccountSettings = repository.syncSettingsForAccount(accountA).first()
        assertFalse(newAccountSettings.backupSetupPromptAnswered)
        assertFalse(newAccountSettings.autoBackupEnabled)

        repository.answerBackupSetupPrompt(accountA, configureFolders = true)
        val configuring = repository.syncSettingsForAccount(accountA).first()
        assertTrue(configuring.backupSetupPromptAnswered)
        assertTrue(configuring.backupSetupPending)
        assertFalse("Choosing folder setup must not upload everything", configuring.autoBackupEnabled)

        val otherAccount = repository.syncSettingsForAccount(accountB).first()
        assertFalse(otherAccount.backupSetupPromptAnswered)
        assertFalse(otherAccount.backupSetupPending)
        assertFalse(otherAccount.autoBackupEnabled)

        repository.updateSelectedSourceIds(accountA, setOf("camera"))
        val configured = repository.syncSettingsForAccount(accountA).first()
        assertTrue(configured.autoBackupEnabled)
        assertFalse(configured.backupSetupPending)
        assertEquals(setOf("camera"), configured.selectedSourceIds)

        repository.answerBackupSetupPrompt(accountB, configureFolders = false)
        repository.enableBackupForAllFolders(accountB)
        val allFolders = repository.syncSettingsForAccount(accountB).first()
        assertTrue(allFolders.backupSetupPromptAnswered)
        assertFalse(allFolders.backupSetupPending)
        assertTrue(allFolders.autoBackupEnabled)
        assertEquals("all", allFolders.sourceMode)
    }
}

package com.iris.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.rememberNavController
import com.iris.app.ui.navigation.IrisNavGraph
import com.iris.app.ui.theme.IrisTheme

class MainActivity : ComponentActivity() {
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        receivePairingCode(intent)
    }

    /** A pairing link opened from outside the app: handed to Settings, which asks before applying it. */
    private fun receivePairingCode(intent: Intent?) {
        val link = intent?.data?.takeIf { it.scheme == "iris" && it.host == "pair" } ?: return
        (application as IrisApplication).pairingRequests.value = link.toString()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = application as IrisApplication
        receivePairingCode(intent)

        setContent {
            IrisTheme(darkTheme = true) {
                val isServerConfigurationReady by app.isServerConfigurationReady.collectAsStateWithLifecycle()
                if (isServerConfigurationReady) {
                    val navController = rememberNavController()
                    IrisNavGraph(
                        navController = navController,
                        application = app
                    )
                } else {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        CircularProgressIndicator()
                    }
                }
            }
        }
    }
}

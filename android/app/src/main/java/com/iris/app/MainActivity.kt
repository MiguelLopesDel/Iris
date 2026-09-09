package com.iris.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.navigation.compose.rememberNavController
import com.iris.app.ui.navigation.IrisNavGraph
import com.iris.app.ui.theme.IrisTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = application as IrisApplication

        setContent {
            IrisTheme(darkTheme = true) {
                val navController = rememberNavController()
                IrisNavGraph(
                    navController = navController,
                    application = app
                )
            }
        }
    }
}

package com.iris.app.ui.screens.settings

import android.os.Build
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisText
import com.iris.app.ui.theme.IrisTextSoft
import com.iris.app.ui.theme.ThemeColors
import com.iris.app.ui.theme.ThemeMode

/** "Aparência": light, dark or the phone's setting, and where the colours come from. */
@Composable
fun AppearanceSettings(
    mode: ThemeMode,
    colors: ThemeColors,
    onMode: (ThemeMode) -> Unit,
    onColors: (ThemeColors) -> Unit,
) {
    Text("Aparência", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = IrisAccent)
    Spacer(Modifier.height(8.dp))
    Text("Tema", fontSize = 15.sp, color = IrisTextSoft)
    Column(Modifier.selectableGroup()) {
        Choice("Claro", "Fundo branco, como a maioria dos apps", mode == ThemeMode.LIGHT) { onMode(ThemeMode.LIGHT) }
        Choice("Escuro", "Fundo preto", mode == ThemeMode.DARK) { onMode(ThemeMode.DARK) }
        Choice("Igual ao celular", "Segue o modo claro ou escuro do sistema", mode == ThemeMode.SYSTEM) {
            onMode(ThemeMode.SYSTEM)
        }
    }
    // Material You colours exist from Android 12 on.
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        Spacer(Modifier.height(8.dp))
        Text("Cores", fontSize = 15.sp, color = IrisTextSoft)
        Column(Modifier.selectableGroup()) {
            Choice("Iris", "As cores do Iris", colors == ThemeColors.IRIS) { onColors(ThemeColors.IRIS) }
            Choice("Do celular", "As cores que o Android tira do seu papel de parede", colors == ThemeColors.SYSTEM) {
                onColors(ThemeColors.SYSTEM)
            }
        }
    }
}

@Composable
private fun Choice(title: String, detail: String, selected: Boolean, onSelect: () -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .selectable(selected = selected, onClick = onSelect, role = Role.RadioButton)
            .padding(vertical = 4.dp)
    ) {
        RadioButton(selected = selected, onClick = null)
        Spacer(Modifier.width(12.dp))
        Column {
            Text(title, fontSize = 16.sp, color = IrisText)
            Text(detail, fontSize = 13.sp, color = IrisTextSoft)
        }
    }
}

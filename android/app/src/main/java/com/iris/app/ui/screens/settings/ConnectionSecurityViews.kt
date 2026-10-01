package com.iris.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.data.remote.security.ConnectionProblem
import com.iris.app.data.remote.security.ConnectionSecurity
import com.iris.app.data.remote.security.TrustMode
import com.iris.app.ui.theme.IrisAccentInk
import com.iris.app.ui.theme.IrisAccentLime
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisDarkSurface
import com.iris.app.ui.theme.IrisTextMuted
import com.iris.app.ui.theme.IrisTextSoft
import java.security.cert.X509Certificate
import java.text.DateFormat

/** What the user may do about a connection problem; each action applies to this server only. */
class ConnectionProblemActions(
    val allowCleartext: () -> Unit,
    val trustDeviceCertificates: () -> Unit,
    val trustPresentedCertificate: () -> Unit,
    val importAuthority: () -> Unit,
    val dismiss: () -> Unit,
)

@Composable
fun ConnectionProblemDialog(problem: ConnectionProblem, actions: ConnectionProblemActions) {
    when (problem) {
        is ConnectionProblem.CleartextNotAllowed -> CleartextDialog(problem, actions)
        is ConnectionProblem.UntrustedCertificate -> UntrustedCertificateDialog(problem, actions)
        // Nothing to choose: the card under the address already explains these.
        is ConnectionProblem.NameMismatch, is ConnectionProblem.NotHttps -> Unit
    }
}

@Composable
private fun CleartextDialog(problem: ConnectionProblem.CleartextNotAllowed, actions: ConnectionProblemActions) {
    AlertDialog(
        onDismissRequest = actions.dismiss,
        title = { Text("Conexão sem criptografia") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("${problem.origin} usa HTTP. Sem criptografia, a senha, as fotos e os vídeos trafegam como estão.")
                Text(
                    if (problem.privateAddress) {
                        "O endereço é de uma rede privada (rede local, VPN ou malha privada). Permita apenas se " +
                            "essa rede for de confiança ou se ela própria já criptografa o tráfego."
                    } else {
                        "Este endereço não é de uma rede privada: o tráfego pode passar por redes de terceiros. " +
                            "Prefira HTTPS."
                    },
                    color = if (problem.privateAddress) IrisTextSoft else IrisDanger,
                )
                Text("A permissão vale só para este servidor e pode ser revogada em Segurança da conexão.",
                    fontSize = 12.sp, color = IrisTextMuted)
            }
        },
        confirmButton = {
            TextButton(onClick = actions.allowCleartext) { Text("Permitir HTTP neste servidor") }
        },
        dismissButton = { TextButton(onClick = actions.dismiss) { Text("Cancelar") } },
    )
}

@Composable
private fun UntrustedCertificateDialog(
    problem: ConnectionProblem.UntrustedCertificate,
    actions: ConnectionProblemActions,
) {
    val top = problem.chain.last()
    AlertDialog(
        onDismissRequest = actions.dismiss,
        title = { Text("Certificado não reconhecido") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "${problem.origin} apresentou um certificado que não foi emitido por uma autoridade pública. " +
                        "Isso é normal num servidor com certificado próprio. Confirme que é o seu servidor " +
                        "comparando a impressão digital com a do certificado configurado nele."
                )
                CertificateDetails("Certificado do servidor", problem.leaf)
                if (top !== problem.leaf) CertificateDetails("Emitido por", top)

                if (problem.trustedByDeviceCas) {
                    Text("A autoridade deste certificado está instalada no aparelho.", color = IrisAccentLime)
                    ChoiceButton("Usar autoridades instaladas no aparelho", primary = true, onClick = actions.trustDeviceCertificates)
                }
                if (problem.canTrustPresentedCertificate) {
                    ChoiceButton("Confiar neste certificado", primary = !problem.trustedByDeviceCas, onClick = actions.trustPresentedCertificate)
                }
                ChoiceButton("Importar certificado da autoridade…", primary = false, onClick = actions.importAuthority)
                if (!problem.trustedByDeviceCas) {
                    ChoiceButton("Usar autoridades instaladas no aparelho", primary = false, onClick = actions.trustDeviceCertificates)
                    Text(
                        "Nenhuma autoridade instalada no aparelho reconhece este certificado agora.",
                        fontSize = 12.sp, color = IrisTextMuted,
                    )
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = actions.dismiss) { Text("Cancelar") } },
    )
}

@Composable
private fun ChoiceButton(label: String, primary: Boolean, onClick: () -> Unit) {
    if (primary) {
        Button(
            onClick = onClick,
            colors = ButtonDefaults.buttonColors(containerColor = IrisAccentLime, contentColor = IrisAccentInk),
            modifier = Modifier.fillMaxWidth(),
        ) { Text(label) }
    } else {
        OutlinedButton(onClick = onClick, modifier = Modifier.fillMaxWidth()) { Text(label) }
    }
}

@Composable
private fun CertificateDetails(title: String, certificate: X509Certificate) {
    val fingerprint = ConnectionSecurity.sha256(certificate)
    Card(
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, fontSize = 12.sp, color = IrisTextMuted)
            Text(commonName(certificate.subjectX500Principal.name), fontWeight = FontWeight.SemiBold)
            Text("Emissor: ${commonName(certificate.issuerX500Principal.name)}", fontSize = 12.sp, color = IrisTextSoft)
            Text(
                "Válido até ${DateFormat.getDateInstance(DateFormat.MEDIUM).format(certificate.notAfter)}",
                fontSize = 12.sp, color = IrisTextSoft,
            )
            Text("SHA-256", fontSize = 11.sp, color = IrisTextMuted)
            Text(
                fingerprint,
                fontSize = 11.sp,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.semantics { contentDescription = "Impressão digital SHA-256 $fingerprint" },
            )
        }
    }
}

private val COMMON_NAME = Regex("""(?:^|,)\s*CN=((?:\\.|[^,])*)""", RegexOption.IGNORE_CASE)

/** The CN of an RFC 2253 name ("CN=iris.example,O=..."), or the whole name when it has none. */
internal fun commonName(distinguishedName: String): String =
    COMMON_NAME.find(distinguishedName)?.groupValues?.get(1)?.replace("\\", "")?.trim()?.ifBlank { null }
        ?: distinguishedName

/** The current server's policy, with a way back to the defaults. */
@Composable
fun ConnectionSecurityCard(summary: SecuritySummary, onReset: () -> Unit) {
    Card(
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = IrisDarkSurface),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                "Segurança da conexão",
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground,
            )
            Text(summary.origin, fontSize = 12.sp, color = IrisTextMuted)
            Text(
                when (summary.trustMode) {
                    TrustMode.SYSTEM -> "Certificados: apenas autoridades públicas (padrão)"
                    TrustMode.DEVICE_CAS -> "Certificados: também autoridades instaladas no aparelho"
                    TrustMode.PINNED -> "Certificados: apenas a autoridade fixada para este servidor"
                },
                fontSize = 13.sp,
                color = IrisTextSoft,
            )
            summary.pinnedFingerprints.forEach {
                Text(it, fontSize = 11.sp, fontFamily = FontFamily.Monospace, color = IrisTextSoft)
            }
            Text(
                if (summary.cleartextAllowed) "HTTP sem criptografia: permitido" else "HTTP sem criptografia: não permitido",
                fontSize = 13.sp,
                color = if (summary.cleartextAllowed && !summary.usesHttps) IrisDanger else IrisTextSoft,
            )
            if (!summary.isDefault) {
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedButton(onClick = onReset, modifier = Modifier.fillMaxWidth()) {
                    Text("Restaurar o padrão para este servidor")
                }
            }
        }
    }
}

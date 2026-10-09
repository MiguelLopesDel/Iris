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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.data.remote.security.ConnectionProblem
import com.iris.app.data.remote.security.ConnectionSecurity
import com.iris.app.data.remote.security.PairingCode
import com.iris.app.data.remote.security.TrustMode
import com.iris.app.data.remote.security.identityCode
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
            val identity = summary.identityKeySha256
            Text(
                if (identity != null) {
                    "Identidade do servidor: fixada pelo pareamento (código ${identityCode(identity)})"
                } else {
                    "Identidade do servidor: não fixada. Pareie pelo código para que o app só fale com este servidor."
                },
                fontSize = 13.sp,
                color = if (identity == null && !summary.usesHttps) IrisDanger else IrisTextSoft,
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

/** Read a pairing code: scan the QR, or paste the link shown under it. */
@Composable
fun PairingEntryDialog(onScan: () -> Unit, onPaste: (String) -> Unit, onDismiss: () -> Unit) {
    var text by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Parear com código") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    "No navegador, em Sistema → Dispositivos conectados → Conectar um celular, o Iris mostra " +
                        "um código QR. Leia o código ou cole o texto que aparece embaixo dele.",
                    fontSize = 13.sp,
                )
                ChoiceButton("Ler código QR", primary = true, onClick = onScan)
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    label = { Text("Ou cole o código (iris://pair…)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onPaste(text) }, enabled = text.isNotBlank()) { Text("Continuar") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancelar") } },
    )
}

/**
 * Asks before the app trusts a server from a pairing code, in plain words: the
 * server's short identity code to compare with the one on the server's page,
 * what trusting means, and the technical details on request.
 */
@Composable
fun PairingConfirmDialog(code: PairingCode, onConfirm: () -> Unit, onCancel: () -> Unit) {
    var showDetails by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text("Confiar neste servidor?") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                val identity = code.identityCode
                if (identity != null) {
                    Text("Código do servidor", fontSize = 14.sp, color = IrisTextSoft)
                    Text(
                        identity,
                        fontSize = 26.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.semantics {
                            contentDescription = "Código do servidor: " + identity.replace("-", " ").toList().joinToString(" ")
                        },
                    )
                    Text(
                        "Confira se é o mesmo código que aparece na tela do Iris onde você leu o QR. " +
                            "Se for diferente, toque em Cancelar.",
                        fontSize = 15.sp,
                    )
                    Text(
                        "Ao confiar, o app passa a aceitar o certificado deste servidor e só conversa com ele: " +
                            "outro aparelho que tente se passar por ele é recusado.",
                        fontSize = 14.sp,
                        color = IrisTextSoft,
                    )
                } else {
                    // Codes from servers without an identity key: only the addresses can be shown.
                    Text(
                        "Este servidor é de uma versão do Iris que não mostra um código de identidade. " +
                            "Só confirme se você leu o QR na tela do seu próprio servidor.",
                        fontSize = 15.sp,
                    )
                }
                if (code.usesCleartext) {
                    Text(
                        "Alguns endereços deste código não usam criptografia (http://). Se o app usar um deles, " +
                            "faça isso só em uma rede de confiança.",
                        fontSize = 14.sp,
                        color = IrisDanger,
                    )
                }
                Text(
                    "Confiar não dá acesso à sua conta: depois, entre com o seu usuário e senha.",
                    fontSize = 14.sp,
                    color = IrisTextSoft,
                )
                TextButton(onClick = { showDetails = !showDetails }) {
                    Text(if (showDetails) "Ocultar detalhes técnicos" else "Mostrar detalhes técnicos")
                }
                if (showDetails) {
                    Text("O app usa o primeiro destes endereços que responder como este servidor:", fontSize = 13.sp)
                    code.addresses.forEach { Text("• $it", fontSize = 13.sp, fontFamily = FontFamily.Monospace) }
                    code.keySha256?.let { sha ->
                        Text("Chave de identidade (SHA-256):", fontSize = 12.sp, color = IrisTextMuted)
                        Text(sha.uppercase().chunked(2).joinToString(":"), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    }
                    code.caSha256?.let { sha ->
                        Text("Autoridade de certificado (SHA-256), que passa a ser confiável para o endereço https que responder como este servidor:",
                            fontSize = 12.sp, color = IrisTextMuted)
                        Text(sha.uppercase().chunked(2).joinToString(":"), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    }
                }
            }
        },
        confirmButton = { Button(onClick = onConfirm) { Text("Confiar") } },
        dismissButton = { TextButton(onClick = onCancel) { Text("Cancelar") } },
    )
}

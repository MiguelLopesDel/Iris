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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.iris.app.R
import com.iris.app.data.remote.security.ConnectionProblem
import com.iris.app.data.remote.security.ConnectionSecurity
import com.iris.app.data.remote.security.PairingCode
import com.iris.app.data.remote.security.TrustMode
import com.iris.app.data.remote.security.identityCode
import com.iris.app.ui.theme.IrisOnAccent
import com.iris.app.ui.theme.IrisAccent
import com.iris.app.ui.theme.IrisDanger
import com.iris.app.ui.theme.IrisSurface
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
        is ConnectionProblem.NameMismatch, is ConnectionProblem.NotHttps, is ConnectionProblem.Unreachable -> Unit
    }
}

@Composable
private fun CleartextDialog(problem: ConnectionProblem.CleartextNotAllowed, actions: ConnectionProblemActions) {
    AlertDialog(
        onDismissRequest = actions.dismiss,
        title = { Text(stringResource(R.string.security_cleartext_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.security_cleartext_uses_http, problem.origin))
                Text(
                    if (problem.privateAddress) {
                        stringResource(R.string.security_cleartext_private)
                    } else {
                        stringResource(R.string.security_cleartext_public)
                    },
                    color = if (problem.privateAddress) IrisTextSoft else IrisDanger,
                )
                Text(stringResource(R.string.security_cleartext_scope),
                    fontSize = 12.sp, color = IrisTextMuted)
            }
        },
        confirmButton = {
            TextButton(onClick = actions.allowCleartext) { Text(stringResource(R.string.security_cleartext_allow)) }
        },
        dismissButton = { TextButton(onClick = actions.dismiss) { Text(stringResource(R.string.common_cancel)) } },
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
        title = { Text(stringResource(R.string.security_cert_title)) },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    stringResource(R.string.security_cert_presented, problem.origin)
                )
                CertificateDetails(stringResource(R.string.security_cert_server), problem.leaf)
                if (top !== problem.leaf) CertificateDetails(stringResource(R.string.security_cert_issued_by), top)

                if (problem.trustedByDeviceCas) {
                    Text(stringResource(R.string.security_cert_device_ca_installed), color = IrisAccent)
                    ChoiceButton(stringResource(R.string.security_cert_use_device_cas), primary = true, onClick = actions.trustDeviceCertificates)
                }
                if (problem.canTrustPresentedCertificate) {
                    ChoiceButton(stringResource(R.string.security_cert_trust_this), primary = !problem.trustedByDeviceCas, onClick = actions.trustPresentedCertificate)
                }
                ChoiceButton(stringResource(R.string.security_cert_import_ca), primary = false, onClick = actions.importAuthority)
                if (!problem.trustedByDeviceCas) {
                    ChoiceButton(stringResource(R.string.security_cert_use_device_cas), primary = false, onClick = actions.trustDeviceCertificates)
                    Text(
                        stringResource(R.string.security_cert_no_device_ca),
                        fontSize = 12.sp, color = IrisTextMuted,
                    )
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = actions.dismiss) { Text(stringResource(R.string.common_cancel)) } },
    )
}

@Composable
private fun ChoiceButton(label: String, primary: Boolean, onClick: () -> Unit) {
    if (primary) {
        Button(
            onClick = onClick,
            colors = ButtonDefaults.buttonColors(containerColor = IrisAccent, contentColor = IrisOnAccent),
            modifier = Modifier.fillMaxWidth(),
        ) { Text(label) }
    } else {
        OutlinedButton(onClick = onClick, modifier = Modifier.fillMaxWidth()) { Text(label) }
    }
}

@Composable
private fun CertificateDetails(title: String, certificate: X509Certificate) {
    val fingerprint = ConnectionSecurity.sha256(certificate)
    val fingerprintDescription = stringResource(R.string.security_cert_fingerprint_description, fingerprint)
    Card(
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = IrisSurface),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, fontSize = 12.sp, color = IrisTextMuted)
            Text(commonName(certificate.subjectX500Principal.name), fontWeight = FontWeight.SemiBold)
            Text(stringResource(R.string.security_cert_issuer, commonName(certificate.issuerX500Principal.name)), fontSize = 12.sp, color = IrisTextSoft)
            Text(
                stringResource(R.string.security_cert_valid_until, DateFormat.getDateInstance(DateFormat.MEDIUM).format(certificate.notAfter)),
                fontSize = 12.sp, color = IrisTextSoft,
            )
            Text("SHA-256", fontSize = 11.sp, color = IrisTextMuted)
            Text(
                fingerprint,
                fontSize = 11.sp,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.semantics { contentDescription = fingerprintDescription },
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
        colors = CardDefaults.cardColors(containerColor = IrisSurface),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                stringResource(R.string.security_card_title),
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground,
            )
            Text(summary.origin, fontSize = 12.sp, color = IrisTextMuted)
            Text(
                when (summary.trustMode) {
                    TrustMode.SYSTEM -> stringResource(R.string.security_trust_system)
                    TrustMode.DEVICE_CAS -> stringResource(R.string.security_trust_device)
                    TrustMode.PINNED -> stringResource(R.string.security_trust_pinned)
                },
                fontSize = 13.sp,
                color = IrisTextSoft,
            )
            summary.pinnedFingerprints.forEach {
                Text(it, fontSize = 11.sp, fontFamily = FontFamily.Monospace, color = IrisTextSoft)
            }
            Text(
                stringResource(if (summary.cleartextAllowed) R.string.security_cleartext_on else R.string.security_cleartext_off),
                fontSize = 13.sp,
                color = if (summary.cleartextAllowed && !summary.usesHttps) IrisDanger else IrisTextSoft,
            )
            val identity = summary.identityKeySha256
            Text(
                if (identity != null) {
                    stringResource(R.string.security_identity_pinned, identityCode(identity))
                } else {
                    stringResource(R.string.security_identity_unpinned)
                },
                fontSize = 13.sp,
                color = if (identity == null && !summary.usesHttps) IrisDanger else IrisTextSoft,
            )
            if (!summary.isDefault) {
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedButton(onClick = onReset, modifier = Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.security_reset_default))
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
        title = { Text(stringResource(R.string.pairing_entry_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    stringResource(R.string.pairing_entry_help),
                    fontSize = 13.sp,
                )
                ChoiceButton(stringResource(R.string.pairing_scan_qr), primary = true, onClick = onScan)
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    label = { Text(stringResource(R.string.pairing_paste_label)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onPaste(text) }, enabled = text.isNotBlank()) { Text(stringResource(R.string.pairing_continue)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.common_cancel)) } },
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
        title = { Text(stringResource(R.string.pairing_confirm_title)) },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                val identity = code.identityCode
                val identityDescription = identity?.let {
                    stringResource(R.string.pairing_server_code_description, it.replace("-", " ").toList().joinToString(" "))
                }.orEmpty()
                if (identity != null) {
                    Text(stringResource(R.string.pairing_server_code), fontSize = 14.sp, color = IrisTextSoft)
                    Text(
                        identity,
                        fontSize = 26.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.semantics { contentDescription = identityDescription },
                    )
                    Text(
                        stringResource(R.string.pairing_compare_code),
                        fontSize = 15.sp,
                    )
                    Text(
                        stringResource(R.string.pairing_trust_effect),
                        fontSize = 14.sp,
                        color = IrisTextSoft,
                    )
                } else {
                    // Codes from servers without an identity key: only the addresses can be shown.
                    Text(
                        stringResource(R.string.pairing_no_identity),
                        fontSize = 15.sp,
                    )
                }
                if (code.usesCleartext) {
                    Text(
                        stringResource(R.string.pairing_cleartext_warning),
                        fontSize = 14.sp,
                        color = IrisDanger,
                    )
                }
                Text(
                    stringResource(R.string.pairing_no_account_access),
                    fontSize = 14.sp,
                    color = IrisTextSoft,
                )
                TextButton(onClick = { showDetails = !showDetails }) {
                    Text(stringResource(if (showDetails) R.string.pairing_hide_details else R.string.pairing_show_details))
                }
                if (showDetails) {
                    Text(stringResource(R.string.pairing_details_addresses), fontSize = 13.sp)
                    code.addresses.forEach { Text("• $it", fontSize = 13.sp, fontFamily = FontFamily.Monospace) }
                    code.keySha256?.let { sha ->
                        Text(stringResource(R.string.pairing_details_key), fontSize = 12.sp, color = IrisTextMuted)
                        Text(sha.uppercase().chunked(2).joinToString(":"), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    }
                    code.caSha256?.let { sha ->
                        Text(stringResource(R.string.pairing_details_ca),
                            fontSize = 12.sp, color = IrisTextMuted)
                        Text(sha.uppercase().chunked(2).joinToString(":"), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    }
                }
            }
        },
        confirmButton = { Button(onClick = onConfirm) { Text(stringResource(R.string.pairing_trust)) } },
        dismissButton = { TextButton(onClick = onCancel) { Text(stringResource(R.string.common_cancel)) } },
    )
}

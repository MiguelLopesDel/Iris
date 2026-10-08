package com.iris.app.ui.screens.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.iris.app.data.model.ServerInfo
import com.iris.app.data.repository.IrisRepository
import com.iris.app.data.repository.ServerSettingsRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.iris.app.data.remote.security.AddressOutcome
import com.iris.app.data.remote.security.ConnectionProblem
import com.iris.app.data.remote.security.PairingCode
import com.iris.app.data.remote.security.PairingCodeException
import com.iris.app.data.remote.security.PairingConnector
import com.iris.app.data.remote.security.ConnectionSecurity
import com.iris.app.data.remote.security.ServerOrigin
import com.iris.app.data.remote.security.ServerSecurity
import com.iris.app.data.remote.security.TrustMode

data class SettingsUiState(
    val serverUrl: String = "",
    val isTestingConnection: Boolean = false,
    val isServerOnline: Boolean? = null,
    val serverMode: String? = null,
    val connectionTestResult: String? = null,
    val isDeviceLoggedIn: Boolean = false,
    val loggedInUsername: String = "",
    val deviceId: String = "",
    val serverInfo: ServerInfo? = null,
    /** A connection-security problem the user can resolve from a dialog. */
    val connectionProblem: ConnectionProblem? = null,
    /** How the app talks to the server being tested. */
    val security: SecuritySummary? = null,
    val securityMessage: String? = null,
    /** A pairing code read and waiting for the user to confirm it. */
    val pendingPairing: PairingCode? = null,
    val isPairing: Boolean = false,
    val pairingMessage: String? = null,
)

data class SecuritySummary(
    val origin: String,
    val usesHttps: Boolean,
    val trustMode: TrustMode,
    val pinnedFingerprints: List<String>,
    val cleartextAllowed: Boolean,
    /** The identity key pinned at pairing, if this server was paired by code. */
    val identityKeySha256: String? = null,
) {
    val isDefault: Boolean get() = trustMode == TrustMode.SYSTEM && !cleartextAllowed
}

class SettingsViewModel(
    private val settingsRepository: ServerSettingsRepository,
    private val irisRepository: IrisRepository,
    private val pairingRequests: MutableStateFlow<String?> = MutableStateFlow(null),
) : ViewModel() {

    private val _uiState = MutableStateFlow(SettingsUiState())
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        // Links opened from outside the app (camera, QR reader) arrive here.
        viewModelScope.launch {
            pairingRequests.collect { link ->
                if (link != null) {
                    pairingRequests.value = null
                    readPairingCode(link)
                }
            }
        }
        viewModelScope.launch {
            val currentUrl = settingsRepository.serverUrl.first()
            _uiState.update { it.copy(serverUrl = currentUrl) }
            if (currentUrl.isNotBlank()) testConnection()
        }
    }

    fun onServerUrlChange(newUrl: String) {
        _uiState.update {
            it.copy(
                serverUrl = newUrl,
                connectionTestResult = null,
                isServerOnline = null
            )
        }
    }

    fun saveAndTestConnection() {
        val url = _uiState.value.serverUrl.trim()
        if (url.isBlank()) return

        viewModelScope.launch {
            settingsRepository.updateServerUrl(url)
            irisRepository.apiClient.updateBaseUrl(url)
            testConnection()
        }
    }

    fun testConnection() {
        if (_uiState.value.serverUrl.isBlank()) {
            _uiState.update {
                it.copy(
                    isTestingConnection = false,
                    isServerOnline = false,
                    serverMode = null,
                    serverInfo = null,
                    connectionTestResult = "Informe o endereço do servidor para testar a conexão."
                )
            }
            return
        }

        viewModelScope.launch {
            val isLoggedIn = irisRepository.credentialsStore.hasValidCredentials()
            val username = irisRepository.credentialsStore.getUsername()
            val deviceId = irisRepository.credentialsStore.getDeviceId() ?: ""

            _uiState.update {
                it.copy(
                    isTestingConnection = true,
                    connectionTestResult = null,
                    isServerOnline = null,
                    isDeviceLoggedIn = isLoggedIn,
                    loggedInUsername = username,
                    deviceId = deviceId
                )
            }

            // Test connection using the unauthenticated /healthz probe
            val healthResult = irisRepository.checkServerHealth()
            if (healthResult.isSuccess) {
                val health = healthResult.getOrThrow()
                var info: ServerInfo? = null
                if (isLoggedIn) {
                    irisRepository.getServerInfo().onSuccess { info = it }
                }

                _uiState.update {
                    it.copy(
                        isTestingConnection = false,
                        isServerOnline = true,
                        serverMode = health.mode,
                        serverInfo = info,
                        connectionTestResult = "Servidor online e acessível (modo: ${health.mode})",
                        connectionProblem = null,
                    )
                }
            } else {
                val failure = healthResult.exceptionOrNull()
                val origin = currentOrigin()
                val problem = if (failure != null && origin != null) {
                    ConnectionProblem.from(failure, origin, security)
                } else null
                val err = failure?.localizedMessage ?: "Servidor inacessível"
                _uiState.update {
                    it.copy(
                        isTestingConnection = false,
                        isServerOnline = false,
                        serverMode = null,
                        serverInfo = null,
                        connectionProblem = problem,
                        connectionTestResult = problem?.let(::describe) ?: "Servidor inacessível: $err"
                    )
                }
            }
            refreshSecuritySummary()
        }
    }

    private val security: ConnectionSecurity get() = irisRepository.apiClient.connectionSecurity

    /** The server actually being contacted, which is the saved address, not the text being edited. */
    private fun currentOrigin(): ServerOrigin? = ServerOrigin.of(irisRepository.apiClient.baseUrl)

    private fun refreshSecuritySummary() {
        val origin = currentOrigin() ?: return
        val current = security.securityFor(origin)
        _uiState.update {
            it.copy(
                security = SecuritySummary(
                    origin = origin.key,
                    usesHttps = irisRepository.apiClient.baseUrl.startsWith("https://"),
                    trustMode = current.trustMode,
                    pinnedFingerprints = current.pinnedCertificates.mapNotNull { der ->
                        ConnectionSecurity.parseCertificates(der).firstOrNull()?.let(ConnectionSecurity::sha256)
                    },
                    cleartextAllowed = current.cleartextAllowed,
                    identityKeySha256 = current.identityKeySha256,
                )
            )
        }
    }

    fun dismissConnectionProblem() = _uiState.update { it.copy(connectionProblem = null) }

    fun allowCleartext() = changeSecurity { it.copy(cleartextAllowed = true) }

    fun trustDeviceCertificates() =
        changeSecurity { it.copy(trustMode = TrustMode.DEVICE_CAS, pinnedCertificates = emptyList()) }

    /** Pins the self-signed top of the chain the server presented. */
    fun trustPresentedCertificate() {
        val problem = _uiState.value.connectionProblem as? ConnectionProblem.UntrustedCertificate ?: return
        if (!problem.canTrustPresentedCertificate) return
        changeSecurity {
            it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(problem.chain.last().encoded))
        }
    }

    /** Pins an authority from a certificate file, if it is the one the server's chain leads to. */
    fun importAuthority(bytes: ByteArray) {
        val origin = currentOrigin() ?: return
        val certificates = runCatching { ConnectionSecurity.parseCertificates(bytes) }.getOrDefault(emptyList())
        if (certificates.isEmpty()) {
            _uiState.update { it.copy(securityMessage = "O arquivo não contém um certificado X.509 (PEM ou DER).") }
            return
        }
        val anchor = certificates.firstOrNull { security.anchorsLastRejected(origin, it) }
        if (anchor == null) {
            _uiState.update {
                it.copy(
                    securityMessage = "Esse certificado não é a autoridade que emitiu o certificado do servidor. " +
                        "Nada foi alterado."
                )
            }
            return
        }
        changeSecurity { it.copy(trustMode = TrustMode.PINNED, pinnedCertificates = listOf(anchor.encoded)) }
    }

    /** Back to the strictest policy: public authorities only, no HTTP. */
    // Certificate trust and HTTP go back to the default; the paired identity stays,
    // since only a new pairing code can say which key the server holds.
    fun resetSecurity() = changeSecurity { ServerSecurity(identityKeySha256 = it.identityKeySha256) }

    fun dismissSecurityMessage() = _uiState.update { it.copy(securityMessage = null) }

    private fun changeSecurity(change: (ServerSecurity) -> ServerSecurity) {
        val origin = currentOrigin() ?: return
        _uiState.update { it.copy(connectionProblem = null, securityMessage = null) }
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                security.update(origin, change)
                // Connections already open were accepted under the previous policy. Closing
                // a TLS connection writes to the network, so never on the main thread.
                irisRepository.apiClient.resetConnections()
            }
            refreshSecuritySummary()
            testConnection()
        }
    }

    private fun describe(problem: ConnectionProblem): String = when (problem) {
        is ConnectionProblem.CleartextNotAllowed ->
            "Este endereço usa HTTP, sem criptografia, e ainda não foi autorizado neste aparelho."
        is ConnectionProblem.UntrustedCertificate ->
            "O certificado do servidor não foi emitido por uma autoridade em que este aparelho confia."
        is ConnectionProblem.NameMismatch ->
            "O certificado é válido, mas não foi emitido para ${problem.origin.host}. Use o endereço que " +
                "consta no certificado ou emita um certificado que inclua este."
        is ConnectionProblem.NotHttps ->
            "Algo respondeu em ${problem.origin}, mas não com HTTPS. Confira a porta, ou use http:// se o " +
                "servidor não usa TLS."
    }

    /** Reads a pairing link and asks the user to confirm it; nothing changes yet. */
    fun readPairingCode(text: String) {
        try {
            val code = PairingCode.parse(text)
            _uiState.update { it.copy(pendingPairing = code, pairingMessage = null) }
        } catch (error: PairingCodeException) {
            _uiState.update { it.copy(pendingPairing = null, pairingMessage = error.message) }
        }
    }

    fun cancelPairing() = _uiState.update { it.copy(pendingPairing = null) }

    fun dismissPairingMessage() = _uiState.update { it.copy(pairingMessage = null) }

    /** Applies the confirmed code. Confirming a code with http:// addresses is the consent to HTTP for them. */
    fun confirmPairing() {
        val code = _uiState.value.pendingPairing ?: return
        _uiState.update { it.copy(pendingPairing = null, isPairing = true, pairingMessage = null) }
        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) {
                runCatching { PairingConnector(security).connect(code, allowCleartext = code.usesCleartext) }
            }
            val result = outcome.getOrNull()
            if (result?.address != null) {
                settingsRepository.updateServerUrl(result.address)
                irisRepository.apiClient.updateBaseUrl(result.address)
                withContext(Dispatchers.IO) { irisRepository.apiClient.resetConnections() }
                _uiState.update {
                    it.copy(isPairing = false, serverUrl = result.address,
                        pairingMessage = "Pareado com o servidor em ${result.address}. Entre com a sua conta.")
                }
                testConnection()
            } else {
                _uiState.update {
                    it.copy(isPairing = false, pairingMessage = outcome.exceptionOrNull()?.message
                        ?: describe(result?.outcomes.orEmpty()))
                }
            }
            refreshSecuritySummary()
        }
    }

    private fun describe(outcomes: List<AddressOutcome>): String =
        "Nenhum endereço do código respondeu como este servidor:\n" + outcomes.joinToString("\n") { outcome ->
            "• ${outcome.address}: " + when (outcome) {
                is AddressOutcome.Connected -> "conectado"
                is AddressOutcome.OtherServer -> "respondeu, mas é outro servidor"
                is AddressOutcome.Skipped -> outcome.reason
                is AddressOutcome.Failed -> outcome.problem?.let(::describe) ?: "não respondeu"
            }
        }

    class Factory(
        private val settingsRepository: ServerSettingsRepository,
        private val irisRepository: IrisRepository,
        private val pairingRequests: MutableStateFlow<String?> = MutableStateFlow(null),
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return SettingsViewModel(settingsRepository, irisRepository, pairingRequests) as T
        }
    }
}

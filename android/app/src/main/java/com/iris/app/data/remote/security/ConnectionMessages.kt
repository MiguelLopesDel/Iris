package com.iris.app.data.remote.security

/**
 * A [ConnectionProblem] in plain words: what happened and what to check, for
 * people who do not know what a port or a certificate is.
 */
object ConnectionMessages {

    fun describe(problem: ConnectionProblem): String = when (problem) {
        is ConnectionProblem.CleartextNotAllowed ->
            "Este endereço usa HTTP, sem criptografia, e ainda não foi autorizado neste aparelho."
        is ConnectionProblem.UntrustedCertificate ->
            "O certificado do servidor não foi emitido por uma autoridade em que este aparelho confia."
        is ConnectionProblem.NameMismatch ->
            "O certificado é válido, mas não foi emitido para ${problem.origin.host}. Use o endereço que " +
                "consta no certificado ou emita um certificado que inclua este."
        is ConnectionProblem.NotHttps ->
            "O servidor em ${problem.origin.host} respondeu na porta ${problem.origin.port}, mas sem HTTPS. " +
                "Confira o número da porta: o Iris usa uma porta para o navegador e outra, com HTTPS, para os celulares."
        is ConnectionProblem.Unreachable -> unreachable(problem)
    }

    private fun unreachable(problem: ConnectionProblem.Unreachable): String {
        val host = problem.origin.host
        return when {
            problem.reason == ConnectionProblem.Unreachable.Reason.UNKNOWN_HOST ->
                if (problem.viaTailscale) {
                    "O nome $host não foi encontrado. Ele é do Tailscale: confira se o Tailscale está ligado neste celular."
                } else {
                    "O nome $host não foi encontrado. Confira se o endereço está escrito certo e se o celular tem internet."
                }
            problem.reason == ConnectionProblem.Unreachable.Reason.REFUSED ->
                "O aparelho em $host respondeu, mas o Iris não atende na porta ${problem.origin.port}. " +
                    "Confira o número da porta e se o Iris está rodando."
            problem.viaTailscale ->
                "O servidor não respondeu. $host é um endereço do Tailscale: confira se o Tailscale está ligado " +
                    "e conectado neste celular, e se o servidor está ligado."
            ConnectionSecurity.isPrivateAddress(host) ->
                "O servidor não respondeu. $host é um endereço da rede de casa: confira se o celular está no " +
                    "mesmo Wi-Fi que o servidor e se o servidor está ligado."
            else ->
                "O servidor em $host não respondeu. Confira se o celular tem internet e se o servidor está ligado."
        }
    }
}

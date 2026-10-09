// "Conectar um celular": the pairing code (QR + link) any signed-in person can show,
// and, for administrators, the addresses and certificate authority it offers.
import { escapeHtml } from './api.js?v=46';
import { openModal } from './ui.js?v=5';

async function request(path, init = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...init });
  if (response.status === 401) { window.location.assign('/login'); throw new Error('Sessão expirada'); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

export function fetchPairing() {
  return request(`/api/pairing?current=${encodeURIComponent(window.location.origin)}`);
}

function groupedFingerprint(hex) {
  return hex.toUpperCase().match(/.{2}/g).join(':');
}

export async function showPairing() {
  let code;
  try {
    code = await fetchPairing();
  } catch (error) {
    openModal({ kicker: 'Celular', title: 'Conectar um celular', body: `<p>Erro: ${escapeHtml(error.message)}</p>` });
    return;
  }
  if (!code.addresses.length) {
    openModal({
      kicker: 'Celular',
      title: 'Conectar um celular',
      body: '<p>Nenhum endereço que um celular consiga usar. Esta página foi aberta pelo endereço local do '
        + 'servidor (127.0.0.1); abra-a pelo endereço que os aparelhos usam, ou peça ao administrador para '
        + 'cadastrar os endereços de acesso em Sistema.</p>',
    });
    return;
  }
  const body = document.createElement('div');
  body.className = 'pairing-body';
  body.innerHTML = `
    <div class="pairing-qr" role="img" aria-label="Código QR de pareamento">${code.qr_svg}</div>
    <ol class="pairing-steps">
      <li>No celular, abra a câmera ou o leitor de QR e aponte para o código; ou, no app Iris,
        use <strong>Configurações → Parear com código</strong>.</li>
      <li>Confira se o app mostra o mesmo <strong>código do servidor</strong> que aparece aqui embaixo,
        toque em <strong>Confiar</strong> e entre com a sua conta.</li>
    </ol>
    ${code.identity_code ? `<p class="pairing-identity">Código do servidor
      <strong>${escapeHtml(code.identity_code)}</strong>
      <span class="system-hint">Se o celular mostrar outro código, cancele: não é este servidor.</span></p>` : ''}
    <p class="system-hint">Endereços oferecidos, em ordem:</p>
    <ul class="pairing-addresses">${code.addresses.map((a) => `<li><code>${escapeHtml(a)}</code></li>`).join('')}</ul>
    ${code.ca_sha256 ? `<p class="system-hint">Autoridade de certificado (SHA-256):<br><code class="pairing-fingerprint">${escapeHtml(groupedFingerprint(code.ca_sha256))}</code></p>` : ''}
    <label class="pairing-link">Ou copie o código como texto
      <input type="text" readonly>
    </label>
    <p class="system-hint">O código não dá acesso a nada sozinho: ele só diz ao app onde está o servidor e em que
      certificado confiar. Entrar na conta continua exigindo a senha.</p>`;
  const linkInput = body.querySelector('.pairing-link input');
  linkInput.value = code.uri;
  linkInput.addEventListener('focus', () => linkInput.select());
  openModal({
    kicker: 'Celular',
    title: 'Conectar um celular',
    body,
    actions: [{
      label: 'Copiar código',
      onClick: async () => {
        try { await navigator.clipboard.writeText(code.uri); } catch (_) { linkInput.select(); }
      },
    }],
  });
}

/** Administrator card: the authority the pairing code points to. */
export async function loadPairingAuthority() {
  const status = document.getElementById('pairing-ca-status');
  const remove = document.getElementById('pairing-ca-remove');
  if (!status) return;
  try {
    const code = await fetchPairing();
    status.innerHTML = code.ca_sha256
      ? `Configurada. SHA-256: <code class="pairing-fingerprint">${escapeHtml(groupedFingerprint(code.ca_sha256))}</code>`
      : 'Nenhuma: os celulares usam as autoridades públicas, ou a escolha feita no próprio app.';
    remove.hidden = !code.ca_sha256;
  } catch (error) {
    status.textContent = `Erro: ${error.message}`;
  }
}

export function bindPairingAuthority() {
  const input = document.getElementById('pairing-ca-file');
  const remove = document.getElementById('pairing-ca-remove');
  const status = document.getElementById('pairing-ca-status');
  input.addEventListener('change', async () => {
    const file = input.files[0];
    input.value = '';
    if (!file) return;
    if (file.size > 65536) { status.textContent = 'Arquivo grande demais para um certificado.'; return; }
    try {
      await request('/api/admin/pairing/ca', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pem: await file.text() }),
      });
      await loadPairingAuthority();
    } catch (error) {
      status.textContent = `Erro: ${error.message}`;
    }
  });
  remove.addEventListener('click', async () => {
    try {
      await request('/api/admin/pairing/ca', { method: 'DELETE' });
      await loadPairingAuthority();
    } catch (error) {
      status.textContent = `Erro: ${error.message}`;
    }
  });
}

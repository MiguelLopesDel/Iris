// "Dispositivos conectados": every phone and browser signed in to this account.
// Disconnecting one invalidates its session; disconnecting this browser signs out.
import { escapeHtml } from './api.js?v=46';
import { confirmModal } from './ui.js?v=5';

const PLATFORM_LABELS = { web: 'Navegador', android: 'Android' };

function lastSeen(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : `visto ${date.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}`;
}

// Unused this long, a sign-in is most likely an old phone or browser.
const STALE_DAYS = 30;

function isStale(device, now = Date.now()) {
  if (device.current || !device.last_seen_at) return false;
  const seen = new Date(device.last_seen_at).getTime();
  return !Number.isNaN(seen) && now - seen > STALE_DAYS * 86400000;
}

function deviceRow(device) {
  return `
    <li class="device-row" data-device="${escapeHtml(device.id)}">
      <span class="device-name">${escapeHtml(device.name)}${device.current ? ' <span class="device-current">este navegador</span>' : ''}</span>
      <span class="device-meta">${escapeHtml(PLATFORM_LABELS[device.platform] || device.platform || '')}${device.last_seen_at ? ' · ' + escapeHtml(lastSeen(device.last_seen_at)) : ''}</span>
      <button class="btn btn-subtle" type="button" data-revoke>${device.current ? 'Sair' : 'Desconectar'}</button>
    </li>`;
}

export async function loadDevices() {
  const list = document.getElementById('devices-list');
  if (!list) return;
  const response = await fetch('/api/sync/devices', { credentials: 'same-origin' });
  if (response.status === 401) { window.location.assign('/login'); return; }
  if (!response.ok) { list.textContent = `Erro: HTTP ${response.status}`; return; }
  const { devices } = await response.json();
  devices.sort((a, b) => (b.current - a.current) || String(b.last_seen_at).localeCompare(String(a.last_seen_at)));
  const recent = devices.filter((device) => !isStale(device));
  const stale = devices.filter((device) => isStale(device));
  // Old sign-ins pile up (every reinstall and every browser adds one): keep
  // them out of the way, and let them all go at once.
  const staleBlock = stale.length ? `
    <li class="device-stale">
      <details>
        <summary>${stale.length} ${stale.length === 1 ? 'aparelho' : 'aparelhos'} sem uso há mais de ${STALE_DAYS} dias</summary>
        <ul class="devices-list">${stale.map(deviceRow).join('')}</ul>
        <button class="btn btn-subtle btn-danger" type="button" data-revoke-stale>Desconectar todos esses</button>
      </details>
    </li>` : '';
  list.innerHTML = (recent.map(deviceRow).join('') + staleBlock) || '<li class="device-row">Nenhum dispositivo conectado.</li>';
  list.querySelectorAll('[data-revoke]').forEach((button) => {
    const row = button.closest('[data-device]');
    const device = devices.find((item) => item.id === row.dataset.device);
    button.addEventListener('click', () => revoke(device));
  });
  list.querySelector('[data-revoke-stale]')?.addEventListener('click', () => revokeStale(stale));
}

async function revokeStale(stale) {
  const confirmed = await confirmModal(
    `${stale.length} ${stale.length === 1 ? 'aparelho perde' : 'aparelhos perdem'} o acesso. Se algum ainda for usado, basta entrar de novo nele.`,
    { title: 'Desconectar aparelhos antigos?', confirmLabel: 'Desconectar todos', danger: true },
  );
  if (!confirmed) return;
  for (const device of stale) {
    await fetch(`/api/sync/devices/${encodeURIComponent(device.id)}`, { method: 'DELETE', credentials: 'same-origin' });
  }
  await loadDevices();
}

async function revoke(device) {
  const confirmed = await confirmModal(
    device.current
      ? 'Este navegador vai sair da conta.'
      : `"${device.name}" vai perder o acesso imediatamente e precisará entrar de novo.`,
    { title: device.current ? 'Sair deste navegador?' : 'Desconectar dispositivo?', confirmLabel: device.current ? 'Sair' : 'Desconectar', danger: true },
  );
  if (!confirmed) return;
  const response = await fetch(`/api/sync/devices/${encodeURIComponent(device.id)}`, { method: 'DELETE', credentials: 'same-origin' });
  if (device.current || response.status === 401) { window.location.assign('/login'); return; }
  await loadDevices();
}

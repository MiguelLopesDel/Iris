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

export async function loadDevices() {
  const list = document.getElementById('devices-list');
  if (!list) return;
  const response = await fetch('/api/sync/devices', { credentials: 'same-origin' });
  if (response.status === 401) { window.location.assign('/login'); return; }
  if (!response.ok) { list.textContent = `Erro: HTTP ${response.status}`; return; }
  const { devices } = await response.json();
  devices.sort((a, b) => (b.current - a.current) || String(b.last_seen_at).localeCompare(String(a.last_seen_at)));
  list.innerHTML = devices.map((device) => `
    <li class="device-row" data-device="${escapeHtml(device.id)}">
      <span class="device-name">${escapeHtml(device.name)}${device.current ? ' <span class="device-current">este navegador</span>' : ''}</span>
      <span class="device-meta">${escapeHtml(PLATFORM_LABELS[device.platform] || device.platform || '')}${device.last_seen_at ? ' · ' + escapeHtml(lastSeen(device.last_seen_at)) : ''}</span>
      <button class="btn btn-subtle" type="button" data-revoke>${device.current ? 'Sair' : 'Desconectar'}</button>
    </li>`).join('') || '<li class="device-row">Nenhum dispositivo conectado.</li>';
  list.querySelectorAll('[data-revoke]').forEach((button) => {
    const row = button.closest('[data-device]');
    const device = devices.find((item) => item.id === row.dataset.device);
    button.addEventListener('click', () => revoke(device));
  });
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

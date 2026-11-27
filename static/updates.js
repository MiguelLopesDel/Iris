// Release updates for administrators: the running version, the newest
// published release, and "update now", which hands the job to the host agent
// (scripts/server.sh updater install). The server restarts during an update,
// so progress is polled until it answers again.

import { applyUpdate, checkForUpdate, getUpdateStatus } from './api.js?v=52';

let poll = null;
let bound = false;

const STATE_TEXT = {
  running: 'Atualização em andamento. O servidor reinicia no fim; esta página volta sozinha.',
  done: 'Última atualização concluída',
  failed: 'A última atualização falhou. O registro está em data/updater/last.log, no servidor.',
};

function formatWhen(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

function render(state) {
  const section = document.getElementById('update-settings');
  const updater = state.updater || {};
  const status = updater.status || {};
  const busy = updater.pending || status.state === 'running';

  document.getElementById('update-current').textContent = state.current || '?';
  document.getElementById('update-latest').textContent = state.latest
    ? state.latest
    : state.enabled ? 'ainda não consultada' : 'consulta desligada (IRIS_UPDATE_CHECK=0)';
  document.getElementById('update-checked').textContent = state.checked_at
    ? `Consultado em ${formatWhen(state.checked_at)}${state.error ? ` · ${state.error}` : ''}`
    : (state.error || '');

  const available = document.getElementById('update-available');
  available.hidden = !state.available;
  available.textContent = state.available ? `Nova versão ${state.latest} disponível.` : '';

  const button = document.getElementById('update-apply');
  button.hidden = !state.available || !updater.installed;
  button.disabled = busy;
  document.getElementById('update-agent-missing').hidden = !state.available || updater.installed;

  const progress = document.getElementById('update-progress');
  const text = busy ? STATE_TEXT.running : STATE_TEXT[status.state];
  progress.hidden = !text;
  progress.classList.toggle('danger-text', status.state === 'failed' && !busy);
  progress.textContent = !text ? '' : status.state === 'done' && !busy
    ? `${text}: ${status.from || '?'} → ${status.to || '?'} em ${formatWhen(status.finished_at)}.`
    : text;

  document.querySelector('[data-nav-for="update-settings"]')?.classList.toggle('has-update', !!state.available);
  section.dataset.busy = busy ? '1' : '';
  return busy;
}

async function refresh(load = getUpdateStatus) {
  clearTimeout(poll);
  try {
    if (render(await load())) poll = setTimeout(refresh, 5000);
  } catch (error) {
    // While the update restarts the server, requests fail: keep waiting.
    if (document.getElementById('update-settings').dataset.busy) {
      poll = setTimeout(refresh, 5000);
    } else {
      document.getElementById('update-checked').textContent = `Erro: ${error.message}`;
    }
  }
}

export function loadUpdates() {
  if (!bound) {
    bound = true;
    document.getElementById('update-check').addEventListener('click', () => refresh(checkForUpdate));
    document.getElementById('update-apply').addEventListener('click', async () => {
      const latest = document.getElementById('update-latest').textContent;
      if (!window.confirm(`Atualizar o servidor para ${latest}? Um backup é feito antes, e o servidor fica fora do ar por alguns minutos.`)) return;
      await refresh(applyUpdate);
    });
  }
  refresh();
}

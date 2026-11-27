/* ── Settings: side navigation, the account's AI choice, the admin AI panel ──
   The settings page is one long column of cards grouped as Minha conta,
   Biblioteca and Administração; the side navigation jumps between them
   without touching the URL hash (which picks the app's tab). */

import { escapeHtml, forgetMyAi, getAdminAi, getMyAi, setMyAi } from './api.js?v=52';
import { confirmModal, toast } from './ui.js?v=7';

// ── Navigation ────────────────────────────────────────────────────────────

let navBound = false;

/* Links whose card is hidden (not an administrator, no local install) go too,
   and so does a group title left with no links under it. */
export function syncSettingsNav() {
  const nav = document.querySelector('.settings-nav');
  if (!nav) return;
  nav.querySelectorAll('a[data-nav-for]').forEach((link) => {
    const target = document.getElementById(link.dataset.navFor);
    link.hidden = !target || target.hidden;
  });
  let title = null;
  let anyVisible = false;
  const close = () => { if (title) title.hidden = !anyVisible; };
  for (const element of nav.children) {
    if (element.classList.contains('settings-nav-group')) {
      close();
      title = element;
      anyVisible = false;
    } else if (!element.hidden) {
      anyVisible = true;
    }
  }
  close();
  // The page's own "Administração" heading follows the navigation's.
  const adminHeading = document.querySelector('[data-group-title="admin"]');
  const adminNav = nav.querySelector('[data-nav-group="admin"]');
  if (adminHeading && adminNav) adminHeading.hidden = adminNav.hidden;
  if (!navBound) bindNav(nav);
}

function bindNav(nav) {
  navBound = true;
  nav.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-nav-for]');
    if (!link) return;
    event.preventDefault();  // the hash belongs to the app's tabs
    document.getElementById(link.dataset.navFor)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setCurrent(link.dataset.navFor);
  });
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (visible) setCurrent(visible.target.id);
  }, { rootMargin: '-20% 0px -70% 0px' });
  nav.querySelectorAll('a[data-nav-for]').forEach((link) => {
    const target = document.getElementById(link.dataset.navFor);
    if (target) observer.observe(target);
  });
}

function setCurrent(id) {
  document.querySelectorAll('.settings-nav a[data-nav-for]').forEach((link) => {
    link.classList.toggle('is-current', link.dataset.navFor === id);
  });
}

// ── The account's choice ──────────────────────────────────────────────────

const WHERE = {
  server: 'As fotos são analisadas no próprio servidor, assim que chegam.',
  worker: 'As fotos são analisadas no computador de processamento que o administrador configurou e o resultado volta para o servidor.',
  off: 'O administrador desligou a análise por IA neste servidor: nenhuma foto está sendo analisada agora.',
};

function number(value) {
  return Number(value || 0).toLocaleString('pt-BR');
}

function renderMyAi(state) {
  document.getElementById('my-ai-enabled').checked = !!state.enabled;
  document.getElementById('my-ai-where').textContent = state.enabled
    ? WHERE[state.mode] || ''
    : 'Desligado: nenhuma foto nova sua é analisada, nem aqui nem em outro computador.';
  const analysed = Math.max(0, (state.items || 0) - (state.without_ai || 0));
  document.getElementById('my-ai-counts').textContent = state.items
    ? `${number(analysed)} de ${number(state.items)} fotos e vídeos já analisados.`
    : '';
}

let myAiBound = false;

export async function loadMyAi() {
  const card = document.getElementById('my-ai');
  if (!card) return;
  try {
    renderMyAi(await getMyAi());
    card.hidden = false;
  } catch {
    card.hidden = true;
  }
  syncSettingsNav();
  if (myAiBound) return;
  myAiBound = true;
  const status = document.getElementById('my-ai-status');
  document.getElementById('my-ai-enabled').addEventListener('change', async (event) => {
    const enabled = event.target.checked;
    status.textContent = 'Salvando...';
    try {
      renderMyAi(await setMyAi(enabled));
      status.textContent = enabled ? 'Análise ligada.' : 'Análise desligada. O que já foi extraído continua guardado.';
    } catch (error) {
      event.target.checked = !enabled;
      status.textContent = `Erro: ${error.message}`;
    }
  });
  document.getElementById('my-ai-forget').addEventListener('click', async () => {
    const ok = await confirmModal(
      'As fotos ficam. Some o que a IA calculou delas: busca pelo conteúdo, texto lido, categorias, rostos e pessoas (inclusive os nomes que você deu).',
      { kicker: 'Privacidade', title: 'Apagar os dados de IA das suas fotos?', confirmLabel: 'Apagar', danger: true },
    );
    if (!ok) return;
    status.textContent = 'Apagando...';
    try {
      const state = await forgetMyAi();
      renderMyAi(state);
      status.textContent = `Pronto: dados de IA de ${number(state.forgotten)} itens apagados.`;
      toast('Dados de IA apagados', 'success');
    } catch (error) {
      status.textContent = `Erro: ${error.message}`;
    }
  });
}

// ── The administrator's panel ─────────────────────────────────────────────

/* The mode is a hidden input the settings form saves; the cards drive it. */
export function syncAiModeCards() {
  const input = document.querySelector('#ai-settings [data-setting="ai_processing"]');
  if (!input) return;
  document.querySelectorAll('#ai-settings [data-ai-mode]').forEach((card) => {
    card.setAttribute('aria-pressed', String(card.dataset.aiMode === input.value));
  });
}

function bindModeCards() {
  const section = document.getElementById('ai-settings');
  if (!section || section.dataset.bound) return;
  section.dataset.bound = 'true';
  section.addEventListener('click', (event) => {
    const card = event.target.closest('[data-ai-mode]');
    if (!card) return;
    section.querySelector('[data-setting="ai_processing"]').value = card.dataset.aiMode;
    syncAiModeCards();
  });
}

function when(iso) {
  if (!iso) return 'nunca';
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

function renderWorkers(workers) {
  const list = document.getElementById('ai-workers');
  if (!workers.length) {
    list.innerHTML = '<li class="device-meta">Nenhum computador de processamento conectado ainda.</li>';
    return;
  }
  list.innerHTML = workers.map((worker) => `
    <li class="device-row">
      <span class="device-name">${escapeHtml(worker.name)}
        <span class="device-badge">${worker.active ? 'trabalhando' : 'parado'}</span></span>
      <span class="device-meta">conta ${escapeHtml(worker.account)} · visto ${escapeHtml(when(worker.last_seen_at))}
        ${worker.done ? ` · ${number(worker.done)} feitos desde que o servidor ligou (${number(worker.per_hour)}/h)` : ''}
        ${worker.failed ? ` · ${number(worker.failed)} com falha` : ''}</span>
    </li>`).join('');
}

function renderAccounts(accounts) {
  const rows = accounts.map((account) => `
    <tr>
      <td>${escapeHtml(account.display_name || account.username)}</td>
      <td>${account.ai_enabled ? 'ligada' : '<span class="danger-text">desligada pela pessoa</span>'}</td>
      <td>${number(account.items)}</td>
      <td>${number(account.without_ai)}</td>
    </tr>`).join('');
  document.getElementById('ai-accounts').innerHTML = `
    <table class="ai-accounts-table">
      <thead><tr><th>Conta</th><th>Análise</th><th>Itens</th><th>Sem IA</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderMigration(state) {
  const box = document.getElementById('ai-migration');
  if (!state.model_next) {
    box.hidden = true;
    return;
  }
  const total = state.accounts.reduce((sum, a) => sum + (a.ai_enabled ? a.items : 0), 0);
  const done = state.accounts.reduce((sum, a) => sum + (a.migrated || 0), 0);
  const switched = state.accounts.filter((a) => a.model === state.model_next).length;
  const percent = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  box.hidden = false;
  box.innerHTML = `
    <p class="system-hint">Trocando o modelo de busca: ${number(done)} de ${number(total)} itens com o modelo novo
      (${percent}%)${switched ? ` · ${switched} conta(s) já trocaram` : ''}. A busca continua no modelo atual até
      cada biblioteca terminar.</p>
    <div class="ai-progress" role="progressbar" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100">
      <span style="width:${percent}%"></span>
    </div>`;
}

async function renderWorkerInstructions() {
  const command = `python desktop/iris_ai_worker.py login ${window.location.origin}`;
  document.getElementById('ai-worker-login').textContent = command;
  try {
    const health = await fetch('/healthz').then((response) => response.json());
    document.getElementById('ai-identity-code').textContent = health.identity_code || '—';
  } catch {
    /* the code is also shown when pairing a phone */
  }
}

export async function loadAdminAi() {
  const section = document.getElementById('ai-settings');
  if (!section) return;
  bindModeCards();
  try {
    const state = await getAdminAi();
    section.hidden = false;
    document.getElementById('ai-server-warning').hidden = state.server_can_process;
    renderWorkers(state.workers);
    renderAccounts(state.accounts);
    renderMigration(state);
    renderWorkerInstructions();
  } catch {
    section.hidden = true;
  }
  syncSettingsNav();
}

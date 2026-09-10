/* ── Iris Pessoas module ────────────────────────────────────────────────────
   Agrupa rostos detectados em pessoas. Nomear, mesclar, remover e navegar as
   mídias de cada pessoa. As mídias abrem na Galeria (evento iris:person). */

import {
  clusterFaces,
  deletePerson,
  escapeHtml,
  faceThumbUrl,
  listPersons,
  mergePersons,
  renamePerson,
} from './api.js?v=40';
import { confirmModal, openModal, promptModal, toast } from './ui.js?v=1';

let _persons = [];

export async function initPersons() {
  const container = document.getElementById('persons-list');
  if (!container) return;
  container.innerHTML = '<p class="filter-empty">Carregando pessoas...</p>';
  try {
    const data = await listPersons();
    _persons = data.persons || [];
    renderPersons();
  } catch (err) {
    container.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
  wireToolbar();
}

function wireToolbar() {
  const cluster = document.getElementById('persons-cluster');
  const recluster = document.getElementById('persons-recluster');
  if (cluster && cluster.dataset.initialized !== 'true') {
    cluster.dataset.initialized = 'true';
    cluster.addEventListener('click', () => runCluster(false));
  }
  if (recluster && recluster.dataset.initialized !== 'true') {
    recluster.dataset.initialized = 'true';
    recluster.addEventListener('click', async () => {
      const ok = await confirmModal(
        'Isso descarta os agrupamentos atuais (e os nomes dados) e refaz tudo do zero.',
        { kicker: 'Pessoas', title: 'Reagrupar do zero?', confirmLabel: 'Reagrupar', danger: true },
      );
      if (ok) runCluster(true);
    });
  }
}

async function runCluster(recluster) {
  const status = document.getElementById('persons-status');
  if (status) status.textContent = recluster ? 'Reagrupando rostos...' : 'Agrupando novos rostos...';
  try {
    const res = await clusterFaces(recluster);
    if (status) status.textContent = '';
    toast(`${res.persons} pessoa(s) · ${res.assigned} rosto(s) atribuído(s)`, 'success');
    await initPersons();
  } catch (err) {
    if (status) status.textContent = '';
    toast('Erro ao agrupar: ' + err.message, 'error');
  }
}

function renderPersons() {
  const container = document.getElementById('persons-list');
  if (!_persons.length) {
    container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">☻</span><p>Nenhuma pessoa ainda.</p><small>Indexe mídias com rostos (ou rode o backfill) e clique em “Agrupar rostos”.</small></div>';
    return;
  }
  container.innerHTML = _persons.map(renderPersonCard).join('');
}

function personCover(p) {
  return p.cover_face_id
    ? `<img src="${faceThumbUrl(p.cover_face_id)}" alt="" loading="lazy">`
    : '<div class="person-cover-empty">?</div>';
}

function renderPersonCard(p) {
  const name = p.name ? escapeHtml(p.name) : '<em>Sem nome</em>';
  return `<article class="person-card" data-person-id="${p.id}">
    <button class="person-cover" data-action="person-open" data-person-id="${p.id}" title="Ver mídias">
      ${personCover(p)}
    </button>
    <div class="person-card-body">
      <div class="person-name" data-action="person-open" data-person-id="${p.id}">${name}</div>
      <div class="person-meta">${p.media_count || 0} mídia(s) · ${p.face_count || 0} rosto(s)</div>
      <div class="person-actions">
        <button class="btn btn-subtle" data-action="person-rename" data-person-id="${p.id}">Renomear</button>
        <button class="btn btn-subtle" data-action="person-merge" data-person-id="${p.id}">Mesclar</button>
        <button class="btn btn-subtle" data-action="person-delete" data-person-id="${p.id}">Remover</button>
      </div>
    </div>
  </article>`;
}

/** Visual person picker. Resolves `{ personId }`, `{ createName }` (when
    `allowCreate` and the user typed a new name) or `null` on cancel. */
export function pickPersonModal({ persons, kicker = 'Pessoas', title = 'Escolher pessoa', allowCreate = false }) {
  return new Promise((resolve) => {
    const body = document.createElement('div');
    const grid = document.createElement('div');
    grid.className = 'person-pick-grid';
    for (const p of persons) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'person-pick';
      btn.innerHTML = `${personCover(p)}
        <span class="person-pick-name">${p.name ? escapeHtml(p.name) : '<em>Sem nome</em>'}</span>
        <span class="person-pick-meta">${p.media_count || 0} mídia(s)</span>`;
      btn.addEventListener('click', () => {
        modal.close();
        resolve({ personId: p.id });
      });
      grid.appendChild(btn);
    }
    if (!persons.length) {
      grid.innerHTML = '<p class="filter-empty">Nenhuma pessoa ainda.</p>';
    }
    body.appendChild(grid);

    if (allowCreate) {
      const form = document.createElement('form');
      form.className = 'person-pick-create';
      form.innerHTML = `
        <input type="text" placeholder="Ou criar nova pessoa…" autocomplete="off">
        <button class="btn btn-primary" type="submit">Criar</button>`;
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const name = form.querySelector('input').value.trim();
        if (!name) return;
        modal.close();
        resolve({ createName: name });
      });
      body.appendChild(form);
    }

    const modal = openModal({
      kicker,
      title,
      body,
      onCancel: () => resolve(null),
    });
  });
}

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-action^="person-"]');
  if (!btn) return;
  const id = parseInt(btn.dataset.personId);
  if (isNaN(id)) return;
  const action = btn.dataset.action;

  if (action === 'person-open') {
    window.dispatchEvent(new CustomEvent('iris:person', { detail: { personId: id } }));
    return;
  }
  if (action === 'person-rename') {
    const current = _persons.find(p => p.id === id);
    const name = await promptModal({
      kicker: 'Pessoas',
      title: 'Nomear pessoa',
      label: 'Nome',
      value: (current && current.name) || '',
      placeholder: 'Ex.: Maria',
    });
    if (name === null) return;
    try {
      await renamePerson(id, name.trim());
      toast(name.trim() ? `Pessoa nomeada: ${name.trim()}` : 'Nome removido', 'success');
      await initPersons();
    } catch (err) {
      toast('Erro ao renomear: ' + err.message, 'error');
    }
    return;
  }
  if (action === 'person-delete') {
    const ok = await confirmModal(
      'Os rostos continuam no catálogo, apenas deixam de estar agrupados nesta pessoa.',
      { kicker: 'Pessoas', title: 'Remover esta pessoa?', confirmLabel: 'Remover', danger: true },
    );
    if (!ok) return;
    try {
      await deletePerson(id);
      toast('Pessoa removida', 'success');
      await initPersons();
    } catch (err) {
      toast('Erro ao remover: ' + err.message, 'error');
    }
    return;
  }
  if (action === 'person-merge') {
    const source = _persons.find(p => p.id === id);
    const others = _persons.filter(p => p.id !== id);
    if (!others.length) {
      toast('Não há outra pessoa para mesclar.', 'info');
      return;
    }
    const choice = await pickPersonModal({
      persons: others,
      title: `Mesclar "${(source && source.name) || 'Sem nome'}" em...`,
    });
    if (!choice) return;
    try {
      await mergePersons(id, choice.personId);
      toast('Pessoas mescladas', 'success');
      await initPersons();
    } catch (err) {
      toast('Erro ao mesclar: ' + err.message, 'error');
    }
    return;
  }
});

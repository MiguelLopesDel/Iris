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
} from './api.js?v=38';

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
    recluster.addEventListener('click', () => {
      if (confirm('Reagrupar do zero? Isso descarta os agrupamentos atuais (e nomes) e refaz tudo.')) {
        runCluster(true);
      }
    });
  }
}

async function runCluster(recluster) {
  const status = document.getElementById('persons-status');
  if (status) status.textContent = recluster ? 'Reagrupando rostos...' : 'Agrupando novos rostos...';
  try {
    const res = await clusterFaces(recluster);
    if (status) status.textContent = `${res.persons} pessoa(s) · ${res.assigned} rosto(s) atribuído(s)`;
    await initPersons();
  } catch (err) {
    if (status) status.textContent = 'Erro: ' + err.message;
  }
}

function renderPersons() {
  const container = document.getElementById('persons-list');
  if (!_persons.length) {
    container.innerHTML = '<p class="filter-empty">Nenhuma pessoa ainda. Indexe mídias com rostos ou rode o backfill e clique em "Agrupar rostos".</p>';
    return;
  }
  container.innerHTML = _persons.map(renderPersonCard).join('');
}

function renderPersonCard(p) {
  const cover = p.cover_face_id
    ? `<img src="${faceThumbUrl(p.cover_face_id)}" alt="" loading="lazy">`
    : '<div class="person-cover-empty">?</div>';
  const name = p.name ? escapeHtml(p.name) : '<em>Sem nome</em>';
  return `<article class="person-card" data-person-id="${p.id}">
    <button class="person-cover" data-action="person-open" data-person-id="${p.id}" title="Ver mídias">
      ${cover}
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
    const name = prompt('Nome da pessoa:', (current && current.name) || '');
    if (name === null) return;
    await renamePerson(id, name.trim());
    await initPersons();
    return;
  }
  if (action === 'person-delete') {
    if (!confirm('Remover esta pessoa? Os rostos continuam no catálogo, apenas deixam de estar agrupados.')) return;
    await deletePerson(id);
    await initPersons();
    return;
  }
  if (action === 'person-merge') {
    const others = _persons.filter(p => p.id !== id);
    if (!others.length) { alert('Não há outra pessoa para mesclar.'); return; }
    const menu = others.map((p, i) => `${i + 1}. ${p.name || '(sem nome)'} — ${p.media_count} mídia(s)`).join('\n');
    const answer = prompt('Mesclar ESTA pessoa em qual? (a atual será absorvida)\n\n' + menu);
    const pos = parseInt(answer, 10) - 1;
    if (isNaN(pos) || pos < 0 || pos >= others.length) return;
    await mergePersons(id, others[pos].id);
    await initPersons();
    return;
  }
});

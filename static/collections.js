/* ── Iris Collections module ────────────────────────────────────────────────
   Albums as a grid of covers; an album opens as a grid of its photos, and a
   photo opens the viewer over the album. */

import { listCollections, createCollection, renameCollection, deleteCollection, getCollectionMembers, removeCollectionMembers, escapeHtml } from './api.js?v=49';
import { openDetail } from './detail.js?v=13';
import { confirmModal, promptModal, toast } from './ui.js?v=5';

let openAlbum = null;  // { id, name, records } while an album is open

export function initCollections() {
  document.getElementById('btn-new-collection').onclick = createAlbum;
  if (openAlbum) showAlbum(openAlbum.id);
  else loadCollections();
}

async function createAlbum() {
  const name = await promptModal({ kicker: 'Álbuns', title: 'Novo álbum', label: 'Nome', placeholder: 'Ex.: Viagens 2026' });
  if (!name || !name.trim()) return;
  await createCollection(name.trim());
  toast('Álbum criado', 'success');
  loadCollections();
}

function countLabel(n) {
  return `${(n || 0).toLocaleString('pt-BR')} ${n === 1 ? 'item' : 'itens'}`;
}

async function loadCollections() {
  openAlbum = null;
  const container = document.getElementById('collections-tab-list');
  document.getElementById('btn-new-collection').hidden = false;
  try {
    const data = await listCollections();
    if (!data.collections.length) {
      container.className = 'entity-list';
      container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">◇</span><p>Nenhum álbum ainda.</p><small>Crie um álbum ou selecione itens em Fotos.</small></div>';
      return;
    }
    container.className = 'album-grid';
    container.innerHTML = data.collections.map((c) => `
      <button type="button" class="album-card" data-album="${c.id}" aria-label="Abrir ${escapeHtml(c.name || '')}">
        <span class="album-cover">${c.cover_url
          ? `<img src="${escapeHtml(c.cover_url)}" alt="" loading="lazy">`
          : '<span class="album-cover-empty" aria-hidden="true">◇</span>'}</span>
        <span class="album-name">${escapeHtml(c.name || '(sem nome)')}</span>
        <span class="album-count">${countLabel(c.count)}</span>
      </button>`).join('');
  } catch (err) {
    container.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

async function showAlbum(id) {
  const container = document.getElementById('collections-tab-list');
  document.getElementById('btn-new-collection').hidden = true;
  container.className = 'album-view';
  container.innerHTML = '<p style="color:var(--text-muted);">Carregando…</p>';
  try {
    const [list, members] = await Promise.all([listCollections(), getCollectionMembers(id)]);
    const album = list.collections.find((c) => c.id === id);
    if (!album) {
      loadCollections();
      return;
    }
    openAlbum = { id, name: album.name || '', records: members.records || [] };
    renderAlbum();
  } catch (err) {
    container.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

function renderAlbum() {
  const container = document.getElementById('collections-tab-list');
  const { name, records } = openAlbum;
  const tiles = records.map((r, pos) => `
    <div class="media-card" title="${escapeHtml(r.arquivo || '')}">
      <div class="media-card-img">
        ${r.thumbnail_url
          ? `<img src="${escapeHtml(r.thumbnail_url)}" alt="${escapeHtml(r.arquivo || '')}" loading="lazy" data-album-open="${pos}">`
          : `<button class="tile-missing" type="button" data-album-open="${pos}" aria-label="Abrir ${escapeHtml(r.arquivo || '')}"><span aria-hidden="true">${r.media_type === 'video' ? '🎬' : '🖼️'}</span></button>`}
        <div class="tile-actions">
          <button class="tile-action" type="button" data-album-remove="${r.db_id}"
                  title="Tirar do álbum" aria-label="Tirar ${escapeHtml(r.arquivo || '')} do álbum">✕</button>
        </div>
      </div>
    </div>`).join('');
  container.innerHTML = `
    <div class="album-head">
      <button type="button" class="btn btn-subtle" data-album-back>← Álbuns</button>
      <div class="album-head-title">
        <h2>${escapeHtml(name || '(sem nome)')}</h2>
        <span class="album-count">${countLabel(records.length)}</span>
      </div>
      <div class="album-head-actions">
        <button type="button" class="btn btn-subtle" data-album-rename>Renomear</button>
        <button type="button" class="btn btn-subtle btn-danger-text" data-album-delete>Excluir álbum</button>
      </div>
    </div>
    ${records.length
      ? `<div class="media-grid timeline album-photos">${tiles}</div>`
      : '<div class="empty-state"><span class="empty-state-icon">◇</span><p>Álbum vazio.</p><small>Selecione fotos em Fotos e use “Adicionar a um álbum”.</small></div>'}`;
}

async function renameAlbum() {
  const name = await promptModal({ kicker: 'Álbuns', title: 'Renomear álbum', label: 'Novo nome', value: openAlbum.name });
  if (!name || !name.trim()) return;
  await renameCollection(openAlbum.id, name.trim());
  toast('Álbum renomeado', 'success');
  openAlbum.name = name.trim();
  renderAlbum();
}

async function deleteAlbum() {
  const ok = await confirmModal('As fotos continuam na biblioteca; só o álbum é removido.', { kicker: 'Álbuns', title: `Excluir “${openAlbum.name}”?`, confirmLabel: 'Excluir', danger: true });
  if (!ok) return;
  await deleteCollection(openAlbum.id);
  toast('Álbum excluído', 'success');
  loadCollections();
}

async function removeFromAlbum(dbId) {
  try {
    await removeCollectionMembers(openAlbum.id, [dbId]);
    openAlbum.records = openAlbum.records.filter((r) => r.db_id !== dbId);
    renderAlbum();
    toast('Tirada do álbum', 'success');
  } catch (err) {
    toast('Erro: ' + err.message, 'error');
  }
}

document.addEventListener('click', (event) => {
  const pane = document.getElementById('tab-collections');
  if (!pane || !pane.contains(event.target)) return;
  const card = event.target.closest('[data-album]');
  if (card) return void showAlbum(parseInt(card.dataset.album, 10));
  if (event.target.closest('[data-album-back]')) return void loadCollections();
  if (event.target.closest('[data-album-rename]')) return void renameAlbum();
  if (event.target.closest('[data-album-delete]')) return void deleteAlbum();
  const remove = event.target.closest('[data-album-remove]');
  if (remove) return void removeFromAlbum(parseInt(remove.dataset.albumRemove, 10));
  const open = event.target.closest('[data-album-open]');
  if (open && openAlbum) openDetail(openAlbum.records, parseInt(open.dataset.albumOpen, 10));
});

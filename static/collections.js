/* ── Iris Collections module ──────────────────────────────────────────────── */

import { listCollections, createCollection, renameCollection, deleteCollection, getCollectionMembers, addCollectionMembers, removeCollectionMembers } from './api.js';

export function initCollections() {
  loadCollections();
  document.getElementById('btn-new-collection').onclick = async () => {
    const name = prompt('Nome da colecao:');
    if (!name) return;
    await createCollection(name);
    loadCollections();
  };
}

async function loadCollections() {
  const container = document.getElementById('collections-tab-list');
  try {
    const data = await listCollections();
    if (!data.collections.length) {
      container.innerHTML = '<p style="color:var(--text-muted);">Nenhuma colecao.</p>';
      return;
    }
    container.innerHTML = data.collections.map(c => `
      <div class="detail-panel" style="margin-bottom:8px;">
        <strong>${c.name}</strong> (${c.count || 0} itens)
        <div style="display:flex;gap:6px;margin-top:6px;">
          <button class="btn" onclick="const n=prompt('Novo nome:');if(n)__irisRenameCol(${c.id},n)">Renomear</button>
          <button class="btn btn-danger" onclick="if(confirm('Deletar?'))__irisDeleteCol(${c.id})">Deletar</button>
        </div>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p style="color:var(--accent);">Erro: ${err.message}</p>`;
  }
}

// Global helpers (TODO: refactor)
window.__irisRenameCol = async (id, name) => { await renameCollection(id, name); loadCollections(); };
window.__irisDeleteCol = async (id) => { await deleteCollection(id); loadCollections(); };

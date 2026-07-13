/* ── Iris Collections module ──────────────────────────────────────────────── */

import { listCollections, createCollection, renameCollection, deleteCollection, getCollectionMembers, addCollectionMembers, removeCollectionMembers, escapeHtml, mediaUrl } from './api.js?v=39';
import { confirmModal, promptModal, toast } from './ui.js?v=1';

var currentColId = null;

export function initCollections() {
  loadCollections();
  document.getElementById('btn-new-collection').onclick = async function() {
    var name = await promptModal({ kicker: 'Coleções', title: 'Nova coleção', label: 'Nome', placeholder: 'Ex.: Viagens 2026' });
    if (!name || !name.trim()) return;
    await createCollection(name.trim());
    toast('Coleção criada', 'success');
    loadCollections();
  };
}

async function loadCollections() {
  var container = document.getElementById('collections-tab-list');
  try {
    var data = await listCollections();
    if (!data.collections.length) {
      container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">◇</span><p>Nenhuma coleção ainda.</p><small>Crie uma com “+ Nova coleção” ou selecione cards na Galeria.</small></div>';
      return;
    }
    container.innerHTML = data.collections.map(function(c) {
      return '<div class="detail-panel" style="margin-bottom:8px;" id="col-panel-' + c.id + '">'
        + '<strong>' + escapeHtml(c.name) + '</strong> (' + (c.count || 0) + ' itens) '
        + '<div style="display:flex;gap:6px;margin:6px 0;">'
        + '<button class="btn" onclick="window.__renameCol(' + c.id + ')">Renomear</button>'
        + '<button class="btn" onclick="window.__deleteCol(' + c.id + ')">Deletar</button>'
        + '<button class="btn" onclick="window.__viewMembers(' + c.id + ')">Ver membros</button>'
        + '</div>'
        + '<div id="col-members-' + c.id + '" style="display:none;margin-top:8px;"></div>'
        + '</div>';
    }).join('');
  } catch (err) {
    container.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>';
  }
}

window.__renameCol = async function(id) {
  var name = await promptModal({ kicker: 'Coleções', title: 'Renomear coleção', label: 'Novo nome' });
  if (!name || !name.trim()) return;
  await renameCollection(id, name.trim());
  toast('Coleção renomeada', 'success');
  loadCollections();
};

window.__deleteCol = async function(id) {
  var ok = await confirmModal('As mídias continuam na biblioteca; apenas o grupo é removido.', { kicker: 'Coleções', title: 'Deletar esta coleção?', confirmLabel: 'Deletar', danger: true });
  if (!ok) return;
  await deleteCollection(id);
  toast('Coleção deletada', 'success');
  loadCollections();
};

window.__viewMembers = async function(colId) {
  var container = document.getElementById('col-members-' + colId);
  if (container.style.display === 'block') {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'block';
  container.innerHTML = '<p style="color:var(--text-muted);">Carregando...</p>';

  try {
    var data = await getCollectionMembers(colId);
    if (!data.records || !data.records.length) {
      container.innerHTML = '<p style="color:var(--text-muted);">Colecao vazia.</p>';
      return;
    }
    var html = '<p style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">'
      + data.records.length + ' item(ns)</p>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;">';
    data.records.forEach(function(r) {
      var thumb = r.thumbnail_url
        ? '<img src="' + escapeHtml(r.thumbnail_url) + '" loading="lazy"'
          + (r.media_type === 'image' ? ' data-lightbox-src="' + escapeHtml(mediaUrl(r.resolved_path)) + '" data-lightbox-title="' + escapeHtml(r.arquivo || '') + '"' : '')
          + ' style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">'
        : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:24px;">' + (r.media_type === 'video' ? '🎬' : '🖼️') + '</div>';
      html += '<div style="font-size:10px;text-align:center;">'
        + thumb
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:2px;" title="' + escapeHtml(r.arquivo || '') + '">' + escapeHtml((r.arquivo || '(sem nome)').slice(0, 30)) + '</div>'
        + '<button class="btn btn-danger" style="font-size:10px;padding:2px 6px;margin-top:2px;" onclick="window.__removeMember(' + colId + ',' + r.db_id + ')">Remover</button>'
        + '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>';
  }
};

window.__removeMember = async function(colId, dbId) {
  try {
    await removeCollectionMembers(colId, [dbId]);
    window.__viewMembers(colId); // refresh
    setTimeout(loadCollections, 1000);
  } catch(err) {
    toast('Erro: ' + err.message, 'error');
  }
};

/* ── Iris Collections module ──────────────────────────────────────────────── */

import { listCollections, createCollection, renameCollection, deleteCollection, getCollectionMembers, addCollectionMembers, removeCollectionMembers } from './api.js';

var currentColId = null;

export function initCollections() {
  loadCollections();
  document.getElementById('btn-new-collection').onclick = async function() {
    var name = prompt('Nome da colecao:');
    if (!name) return;
    await createCollection(name);
    loadCollections();
  };
}

async function loadCollections() {
  var container = document.getElementById('collections-tab-list');
  try {
    var data = await listCollections();
    if (!data.collections.length) {
      container.innerHTML = '<p style="color:var(--text-muted);">Nenhuma colecao.</p>';
      return;
    }
    container.innerHTML = data.collections.map(function(c) {
      return '<div class="detail-panel" style="margin-bottom:8px;" id="col-panel-' + c.id + '">'
        + '<strong>' + c.name + '</strong> (' + (c.count || 0) + ' itens) '
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
  var name = prompt('Novo nome:');
  if (!name) return;
  await renameCollection(id, name);
  loadCollections();
};

window.__deleteCol = async function(id) {
  if (!confirm('Deletar colecao?')) return;
  await deleteCollection(id);
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
        ? '<img src="' + r.thumbnail_url + '" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">'
        : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:24px;">' + (r.media_type === 'video' ? '🎬' : '🖼️') + '</div>';
      html += '<div style="font-size:10px;text-align:center;">'
        + thumb
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:2px;" title="' + r.arquivo + '">' + r.arquivo.slice(0, 30) + '</div>'
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
    alert('Erro: ' + err.message);
  }
};

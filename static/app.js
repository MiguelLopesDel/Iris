/* ── Iris App controller ───────────────────────────────────────────────────
   Tab routing, sidebar filters, selection state, floating panel. */

import { fetchInfo, listCollections, listConcepts, trashRecords } from './api.js';
import { initGallery, invalidateCache } from './gallery.js';
import { initSearch, doSimilarSearch, doRandomSearch } from './search.js';
import { initCollections } from './collections.js';
import { initConcepts } from './concepts.js';
import { initDuplicates } from './duplicates.js';

window.__irisSelection = window.__irisSelection || new Map();

// ── Tab routing ──────────────────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  var btn = document.querySelector('[data-tab="' + name + '"]');
  if (btn) btn.classList.add('active');
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  var pane = document.getElementById('tab-' + name);
  if (pane) pane.classList.add('active');
  document.getElementById('search-bar').style.display = (name === 'search') ? 'flex' : 'none';
  if (name === 'gallery') initGallery();
  if (name === 'search') initSearch();
  if (name === 'collections') initCollections();
  if (name === 'concepts') initConcepts();
  if (name === 'duplicates') initDuplicates();
  window.location.hash = name;
}

// ── Custom events ─────────────────────────────────────────────────────────

window.addEventListener('iris:similar', function(e) {
  switchTab('search');
  doSimilarSearch(e.detail.index);
});

window.addEventListener('iris:detail', async function(e) {
  var index = e.detail.index;
  var card = document.querySelector('.media-card[data-index="' + index + '"]');
  if (!card) return;
  var old = document.getElementById('detail-panel');
  if (old) old.remove();

  var panel = document.createElement('div');
  panel.id = 'detail-panel';
  panel.className = 'detail-panel';
  panel.innerHTML = '<p style="color:var(--text-muted);">Carregando...</p>';
  card.after(panel);

  try {
    var res = await fetch('/api/records/' + index);
    if (!res.ok) throw new Error('Record not found');
    var r = await res.json();
    var hasFile = r.resolved_path && r.resolved_path !== 'None';
    var ext = (r.arquivo || '').split('.').pop().toLowerCase();
    var isVideo = ['mp4','webm','mkv','mov','ogg'].indexOf(ext) >= 0;
    var inColIds = {};
    (r.collections || []).forEach(function(c) { inColIds[c.id] = true; });
    var inConcIds = {};
    (r.concepts || []).forEach(function(c) { if (c.confirmed) inConcIds[c.id] = true; });

    var html = '<div style="display:flex;gap:12px;align-items:start;">';
    if (r.thumbnail_url) html += '<img src="' + r.thumbnail_url + '" style="width:200px;border-radius:8px;">';
    html += '<div style="flex:1;min-width:0;">';
    html += '<h4 style="word-break:break-all;">' + (r.arquivo || '(sem nome)') + '</h4>';
    if (hasFile) html += '<pre style="font-size:10px;max-width:100%;overflow-x:auto;background:var(--bg-primary);padding:4px;border-radius:4px;">' + r.resolved_path + '</pre>';
    html += '<p style="font-size:11px;color:var(--text-muted);">Indice: ' + index + ' · DB ID: ' + r.db_id + ' · ' + (isVideo ? '🎬 Video' : '🖼️ Imagem') + ' · ' + (r.file_size ? Math.round(r.file_size/1024) + 'KB' : '?') + '</p>';
    html += '</div></div>';

    if (r.texto_extraido) html += '<div style="margin-top:8px;"><strong style="font-size:11px;">Texto extraido:</strong><pre style="font-size:10px;max-height:80px;overflow-y:auto;">' + r.texto_extraido + '</pre></div>';
    if (r.descricao_ia) html += '<div style="margin-top:4px;"><strong style="font-size:11px;">Descricao IA:</strong><pre style="font-size:10px;max-height:60px;overflow-y:auto;">' + r.descricao_ia + '</pre></div>';
    if (r.tags) html += '<div style="margin-top:4px;"><strong style="font-size:11px;">Tags:</strong> <span style="font-size:11px;">' + r.tags + '</span></div>';

    html += '<div style="margin-top:8px;font-size:11px;"><strong>Colecoes:</strong><div id="detail-cols" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:2px;">Carregando...</div></div>';
    html += '<div style="margin-top:8px;font-size:11px;"><strong>Conceitos:</strong><div id="detail-concs" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:2px;">Carregando...</div></div>';

    if (r.style || r.source_work || r.context || r.humor) {
      html += '<div style="margin-top:8px;font-size:11px;color:var(--text-secondary);">';
      if (r.style) html += 'Estilo: ' + r.style + ' · ';
      if (r.source_work) html += 'Obra: ' + r.source_work + ' · ';
      if (r.context) html += 'Contexto: ' + r.context + ' · ';
      if (r.humor) html += 'Humor: ' + r.humor;
      html += '</div>';
    }

    html += '<div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;">';
    if (hasFile) {
      var folder = r.resolved_path.replace(/\/[^/]+$/, '');
      html += '<a href="file://' + folder + '" class="btn" style="font-size:11px;">📁 Abrir pasta</a>';
      html += '<a href="/media/' + r.resolved_path + '" class="btn" style="font-size:11px;" target="_blank">📄 Abrir arquivo</a>';
    }
    html += '<button class="btn" style="font-size:11px;" onclick="window.dispatchEvent(new CustomEvent(\'iris:similar\',{detail:{index:' + index + '}}))">🔍 Similares</button>';
    html += '<button class="btn" style="font-size:11px;" onclick="this.closest(\'#detail-panel\').remove()">Fechar</button>';
    html += '</div>';

    panel.innerHTML = html;

    // Fetch all collections/concepts for toggle buttons
    try {
      var colData = await fetch('/api/collections').then(function(r){return r.json();});
      var concData = await fetch('/api/concepts').then(function(r){return r.json();});

      var colsEl = document.getElementById('detail-cols');
      if (colsEl && colData.collections) {
        colsEl.innerHTML = colData.collections.map(function(c) {
          var inCol = inColIds[c.id];
          return '<button class="btn" style="font-size:10px;padding:2px 6px;" onclick="window.__toggleCollection(' + c.id + ',' + r.db_id + ',' + (!inCol) + ',' + index + ')">' + (inCol ? '[x]' : '[ ]') + ' ' + c.name + '</button>';
        }).join('') || '(nenhuma)';
      }

      var concsEl = document.getElementById('detail-concs');
      if (concsEl && concData.concepts) {
        concsEl.innerHTML = concData.concepts.map(function(c) {
          var inConc = inConcIds[c.id];
          return '<button class="btn" style="font-size:10px;padding:2px 6px;" onclick="window.__toggleConcept(' + c.id + ',' + r.db_id + ',' + (!inConc) + ',' + index + ')">' + (inConc ? '[x]' : '[ ]') + ' ' + c.name + '</button>';
        }).join('') || '(nenhum)';
      }
    } catch(e) { console.warn('toggle load failed', e); }

  } catch (err) {
    panel.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p><button class="btn" onclick="this.parentElement.remove()">Fechar</button>';
  }
});

// ── Sidebar build ────────────────────────────────────────────────────────

async function buildSidebar() {
  try {
    var info = await fetchInfo();
    document.getElementById('status-badge').textContent = info.total_records + ' itens';

    var colData = await listCollections();
    var colList = document.getElementById('collections-list');
    if (colData.collections && colData.collections.length) {
      colList.innerHTML = colData.collections.map(function(c) {
        return '<label class="filter-checkbox"><input type="checkbox" value="' + c.id + '" class="collection-filter"> ' + c.name + ' (' + (c.count || 0) + ')</label>';
      }).join('');
    }

    var concData = await listConcepts();
    var concList = document.getElementById('concepts-list');
    if (concData.concepts && concData.concepts.length) {
      concList.innerHTML = concData.concepts.map(function(c) {
        return '<label class="filter-checkbox"><input type="checkbox" value="' + c.id + '" class="concept-filter"> ' + c.name + ' (' + (c.assoc_count || 0) + ')</label>';
      }).join('');
    }

    document.querySelectorAll('.collection-filter, .concept-filter, #filtro-media-type').forEach(function(el) {
      el.addEventListener('change', function() { invalidateCache(); });
    });
  } catch (err) {
    document.getElementById('status-badge').textContent = 'offline';
  }
}

// ── Floating selection panel ─────────────────────────────────────────────

window.addEventListener('iris:selection-changed', function() {
  var n = window.__irisSelection.size;
  var panel = document.getElementById('floating-panel');
  if (n === 0) { panel.style.display = 'none'; return; }
  panel.style.display = 'flex';
  document.getElementById('floating-count').textContent = n + ' selecionado(s)';
  var indices = Array.from(window.__irisSelection.keys()).slice(0, 8);
  document.getElementById('floating-thumbs').innerHTML = indices.map(function(i) {
    var card = document.querySelector('.media-card[data-index="' + i + '"]');
    var img = card ? card.querySelector('img') : null;
    return img ? '<img src="' + img.src + '" alt="">' : '';
  }).join('');
});

// ── Trash selected ───────────────────────────────────────────────────────

document.getElementById('btn-trash-selected').addEventListener('click', async function() {
  var ids = Array.from(window.__irisSelection.keys());
  if (!ids.length) return;
  if (!confirm('Mover ' + ids.length + ' item(ns) para lixeira?')) return;
  try {
    var result = await trashRecords(ids);
    toast('Movidos: ' + result.moved + ', Falhas: ' + result.failed, result.failed ? 'error' : 'success');
    window.__irisSelection.clear();
    window.dispatchEvent(new CustomEvent('iris:selection-changed'));
    window.location.reload();
  } catch (err) {
    toast('Erro: ' + err.message, 'error');
  }
});

// ── Toast ────────────────────────────────────────────────────────────────

function toast(msg, level) {
  level = level || 'info';
  var container = document.getElementById('toast-container');
  var el = document.createElement('div');
  el.className = 'toast ' + level;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(function() { el.remove(); }, 3500);
}

// ── Collection/Concept toggle helpers ────────────────────────────────────

window.__toggleCollection = async function(colId, dbId, add, idx) {
  try {
    var url = add ? '/api/collections/' + colId + '/members' : '/api/collections/' + colId + '/members/remove';
    await fetch(url, { method: 'POST', body: new URLSearchParams({ db_ids: String(dbId) }) });
    toast(add ? 'Adicionado a colecao' : 'Removido da colecao', 'success');
    var panel = document.getElementById('detail-panel');
    if (panel) panel.remove();
    window.dispatchEvent(new CustomEvent('iris:detail', { detail: { index: idx } }));
  } catch(e) { toast('Erro: ' + e.message, 'error'); }
};

window.__toggleConcept = async function(concId, dbId, confirm, idx) {
  try {
    var url = confirm ? '/api/concepts/' + concId + '/confirm' : '/api/concepts/' + concId + '/reject';
    await fetch(url, { method: 'POST', body: new URLSearchParams({ db_ids: String(dbId) }) });
    toast(confirm ? 'Confirmado no conceito' : 'Rejeitado do conceito', 'success');
    var panel = document.getElementById('detail-panel');
    if (panel) panel.remove();
    window.dispatchEvent(new CustomEvent('iris:detail', { detail: { index: idx } }));
  } catch(e) { toast('Erro: ' + e.message, 'error'); }
};

// ── Search mode & parameter controls ────────────────────────────────────

document.getElementById('search-mode').addEventListener('change', function() {
  var custom = document.getElementById('search-custom-params');
  custom.style.display = this.value === 'custom' ? 'block' : 'none';
});

['search-balance','search-textbonus','search-lexical','search-threshold','search-topk'].forEach(function(id) {
  var el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('input', function() {
    var spanId = id.replace('search-', '') + '-val';
    var span = document.getElementById(spanId);
    if (span) span.textContent = parseFloat(this.value).toFixed(2);
  });
});

document.getElementById('btn-surprise').addEventListener('click', function() {
  switchTab('search');
  doRandomSearch(parseInt(document.getElementById('search-topk').value) || 50);
});

document.getElementById('btn-refresh').addEventListener('click', function() {
  invalidateCache();
  window.dispatchEvent(new CustomEvent('iris:selection-changed'));
  toast('Dados atualizados', 'info');
});

// ── Video volume sync (matches Streamlit behavior) ──────────────────────

var _videoVolume = 0.3;
document.addEventListener('volumechange', function(e) {
  if (e.target.matches('video')) {
    _videoVolume = e.target.volume;
    document.querySelectorAll('video').forEach(function(v) {
      if (v !== e.target) v.volume = _videoVolume;
    });
  }
}, true);

// Set initial volume for any video that starts playing
document.addEventListener('play', function(e) {
  if (e.target.matches('video') && e.target.volume !== _videoVolume) {
    e.target.volume = _videoVolume;
  }
}, true);

// ── Statistics (extension chart) ──────────────────────────────────────────

window.__showStats = async function() {
  var el = document.getElementById('stats-container');
  if (el.style.display === 'block') { el.style.display = 'none'; return; }
  el.style.display = 'block';
  el.innerHTML = '<p style="color:var(--text-muted);">Carregando...</p>';
  try {
    var res = await fetch('/api/records?page=1&per_page=500&sort_by=importacao');
    var data = await res.json();
    // Count extensions client-side
    var counts = {};
    var allRecords = data.records;
    // We need all records for accurate stats — use a large page
    var res2 = await fetch('/api/records?page=1&per_page=500&sort_by=nome');
    var data2 = await res2.json();
    data2.records.forEach(function(r) {
      var ext = (r.arquivo || '').split('.').pop().toLowerCase();
      counts[ext] = (counts[ext] || 0) + 1;
    });
    var sorted = Object.entries(counts).sort(function(a, b) { return b[1] - a[1]; });
    var maxCount = sorted.length ? sorted[0][1] : 1;
    var html = '<p style="font-size:11px;margin-bottom:4px;">Extensoes (amostra de ' + data2.records.length + ')</p>';
    sorted.slice(0, 15).forEach(function(e) {
      var pct = Math.round(e[1] / maxCount * 100);
      html += '<div style="font-size:10px;margin:2px 0;">'
        + '<span style="display:inline-block;width:50px;">' + e[0] + '</span>'
        + '<span style="display:inline-block;background:var(--accent);height:10px;border-radius:2px;width:' + pct + '%;min-width:2px;"></span> '
        + '<span>' + e[1] + '</span></div>';
    });
    el.innerHTML = html;
  } catch(e) { el.innerHTML = '<p style="color:var(--accent);">Erro</p>'; }
};

// ── Init ─────────────────────────────────────────────────────────────────

(function init() {
  document.querySelectorAll('.tab-btn').forEach(function(btn) {
    btn.addEventListener('click', function() { switchTab(btn.dataset.tab); });
  });
  window.addEventListener('hashchange', function() {
    switchTab(window.location.hash.slice(1) || 'gallery');
  });
  buildSidebar();
  switchTab(window.location.hash.slice(1) || 'gallery');
})();

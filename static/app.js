/* ── Iris App controller ───────────────────────────────────────────────────
   Tab routing, sidebar filters, selection state, floating panel. */

import {
  addCollectionMembers,
  escapeHtml,
  fetchInfo,
  listCollections,
  listConcepts,
  trashRecords
} from './api.js?v=24';
import { initGallery, invalidateCache } from './gallery.js?v=24';
import { initSearch, doSimilarSearch, doRandomSearch } from './search.js?v=24';
import { initCollections } from './collections.js?v=24';
import { initConcepts } from './concepts.js?v=24';
import { initDuplicates } from './duplicates.js?v=24';
import { initSystem } from './system.js?v=24';

window.__irisSelection = window.__irisSelection || new Map();

// ── Tab routing ──────────────────────────────────────────────────────────

function switchTab(name) {
  var viewMeta = {
    gallery: {
      kicker: 'Biblioteca',
      title: 'Galeria',
      description: 'Navegue por toda a sua colecao visual.'
    },
    search: {
      kicker: 'Descoberta',
      title: 'Busca multimodal',
      description: 'Encontre midias por texto, imagem ou similaridade.'
    },
    collections: {
      kicker: 'Organizacao',
      title: 'Colecoes',
      description: 'Agrupe e mantenha seus conjuntos importantes por perto.'
    },
    concepts: {
      kicker: 'Semantica',
      title: 'Conceitos',
      description: 'Ensine entidades e contextos recorrentes ao Iris.'
    },
    duplicates: {
      kicker: 'Manutencao',
      title: 'Duplicatas',
      description: 'Revise arquivos visualmente proximos com seguranca.'
    },
    system: {
      kicker: 'Operacao',
      title: 'Sistema',
      description: 'Configure, importe, indexe e proteja a biblioteca.'
    }
  };
  var meta = viewMeta[name] || viewMeta.gallery;
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
  if (name === 'system') initSystem();
  document.getElementById('view-kicker').textContent = meta.kicker;
  document.getElementById('view-title').textContent = meta.title;
  document.getElementById('view-description').textContent = meta.description;
  document.body.classList.remove('sidebar-open');
  document.getElementById('sidebar-toggle').setAttribute('aria-expanded', 'false');
  window.location.hash = name;
}

// ── Custom events ─────────────────────────────────────────────────────────

window.addEventListener('iris:similar', function(e) {
  switchTab('search');
  doSimilarSearch(e.detail.index);
});

window.addEventListener('iris:detail', async function(e) {
  var index = e.detail.index;

  // Toggle: if detail panel already open for the same item, close it
  var old = document.getElementById('detail-panel');
  if (old) {
    var currentIndex = parseInt(old.dataset.detailIndex);
    if (currentIndex === index) {
      old.remove();
      return;
    }
    old.remove();
  }

  var card = document.querySelector('.media-card[data-index="' + index + '"]');
  if (!card) return;

  var panel = document.createElement('div');
  panel.id = 'detail-panel';
  panel.className = 'detail-panel';
  panel.dataset.detailIndex = index;
  panel.innerHTML = '<p style="color:var(--text-muted);">Carregando...</p>';

  // Insert after the card; CSS grid-column: 1/-1 makes it span full width
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

    var html = '<div style="display:flex;gap:16px;align-items:start;flex-wrap:wrap;">';
    if (r.thumbnail_url) html += '<img src="' + escapeHtml(r.thumbnail_url) + '" style="width:260px;max-width:100%;border-radius:8px;flex-shrink:0;">';
    html += '<div style="flex:1;min-width:280px;">';
    html += '<h3 style="word-break:break-all;margin-bottom:6px;">' + escapeHtml(r.arquivo || '(sem nome)') + '</h3>';
    if (hasFile) html += '<pre style="font-size:11px;max-width:100%;overflow-x:auto;background:var(--bg-primary);padding:6px 8px;border-radius:4px;white-space:pre-wrap;word-break:break-all;">' + escapeHtml(r.resolved_path) + '</pre>';
    html += '<p style="font-size:12px;color:var(--text-muted);margin-top:4px;">Indice: ' + index + ' · DB ID: ' + r.db_id + ' · ' + (isVideo ? '🎬 Video' : '🖼️ Imagem') + ' · ' + (r.file_size != null ? Math.round(r.file_size/1024) + 'KB' : '?') + '</p>';
    html += '</div></div>';

    // Metadata rows — use a two-column layout for better readability
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">';

    if (r.texto_extraido) {
      html += '<div><strong style="font-size:12px;">📝 Texto extraido:</strong><pre style="font-size:11px;max-height:120px;overflow-y:auto;margin-top:4px;line-height:1.5;">' + escapeHtml(r.texto_extraido) + '</pre></div>';
    }
    if (r.descricao_ia) {
      html += '<div><strong style="font-size:12px;">🤖 Descricao IA:</strong><pre style="font-size:11px;max-height:120px;overflow-y:auto;margin-top:4px;line-height:1.5;">' + escapeHtml(r.descricao_ia) + '</pre></div>';
    }
    html += '</div>';

    if (r.tags) html += '<div style="margin-top:8px;"><strong style="font-size:12px;">🏷 Tags:</strong> <span style="font-size:12px;">' + escapeHtml(r.tags) + '</span></div>';

    if (r.style || r.source_work || r.context || r.humor) {
      html += '<div style="margin-top:8px;font-size:12px;color:var(--text-secondary);display:flex;flex-wrap:wrap;gap:4px 16px;">';
      if (r.style) html += '<span>🎨 Estilo: <strong>' + escapeHtml(r.style) + '</strong></span>';
      if (r.source_work) html += '<span>📖 Obra: <strong>' + escapeHtml(r.source_work) + '</strong></span>';
      if (r.context) html += '<span>🌍 Contexto: <strong>' + escapeHtml(r.context) + '</strong></span>';
      if (r.humor) html += '<span>😂 Humor: <strong>' + escapeHtml(r.humor) + '</strong></span>';
      html += '</div>';
    }

    html += '<div style="margin-top:10px;font-size:12px;"><strong>📁 Colecoes:</strong><div id="detail-cols" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">Carregando...</div></div>';
    html += '<div style="margin-top:8px;font-size:12px;"><strong>🏷 Conceitos:</strong><div id="detail-concs" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">Carregando...</div></div>';

    html += '<div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;padding-top:10px;border-top:1px solid var(--border);">';
    if (hasFile) {
      var folder = r.resolved_path.replace(/\/[^/]+$/, '');
      html += '<a href="file://' + folder + '" class="btn" style="font-size:12px;padding:6px 12px;">📁 Abrir pasta</a>';
      html += '<a href="/media/' + r.resolved_path + '" class="btn" style="font-size:12px;padding:6px 12px;" target="_blank">📄 Abrir arquivo</a>';
    }
    html += '<button class="btn" style="font-size:12px;padding:6px 12px;" onclick="window.dispatchEvent(new CustomEvent(\'iris:similar\',{detail:{index:' + index + '}}))">🔍 Similares</button>';
    html += '<button class="btn" style="font-size:12px;padding:6px 12px;" onclick="this.closest(\'#detail-panel\').remove()">✕ Fechar</button>';
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
          return '<button class="btn" style="font-size:11px;padding:4px 10px;" onclick="window.__toggleCollection(' + c.id + ',' + r.db_id + ',' + (!inCol) + ',' + index + ')">' + (inCol ? '✓' : '＋') + ' ' + escapeHtml(c.name) + '</button>';
        }).join('') || '<span style="color:var(--text-muted);font-size:11px;">(nenhuma)</span>';
      }

      var concsEl = document.getElementById('detail-concs');
      if (concsEl && concData.concepts) {
        concsEl.innerHTML = concData.concepts.map(function(c) {
          var inConc = inConcIds[c.id];
          return '<button class="btn" style="font-size:11px;padding:4px 10px;" onclick="window.__toggleConcept(' + c.id + ',' + r.db_id + ',' + (!inConc) + ',' + index + ')">' + (inConc ? '✓' : '＋') + ' ' + escapeHtml(c.name) + '</button>';
        }).join('') || '<span style="color:var(--text-muted);font-size:11px;">(nenhum)</span>';
      }
    } catch(e) { console.warn('toggle load failed', e); }

  } catch (err) {
    panel.textContent = '';
    var errP = document.createElement('p');
    errP.style.color = 'var(--accent)';
    errP.textContent = 'Erro: ' + err.message;
    panel.appendChild(errP);
    var closeBtn = document.createElement('button');
    closeBtn.className = 'btn';
    closeBtn.textContent = 'Fechar';
    closeBtn.onclick = function() { panel.remove(); };
    panel.appendChild(closeBtn);
  }
});

// ── Sidebar build ────────────────────────────────────────────────────────

async function buildSidebar() {
  try {
    var info = await fetchInfo();
    document.getElementById('status-badge').innerHTML =
      '<i></i>' + info.total_records + ' itens';

    var colData = await listCollections();
    var colList = document.getElementById('collections-list');
    if (colData.collections && colData.collections.length) {
      colList.innerHTML = colData.collections.map(function(c) {
        return '<label class="filter-checkbox"><input type="checkbox" value="' + c.id + '" class="collection-filter"> ' + escapeHtml(c.name) + ' (' + (c.count || 0) + ')</label>';
      }).join('');
    } else {
      colList.innerHTML = '<span class="filter-empty">Nenhuma colecao criada</span>';
    }

    var concData = await listConcepts();
    var concList = document.getElementById('concepts-list');
    if (concData.concepts && concData.concepts.length) {
      concList.innerHTML = concData.concepts.map(function(c) {
        return '<label class="filter-checkbox"><input type="checkbox" value="' + c.id + '" class="concept-filter"> ' + escapeHtml(c.name) + ' (' + (c.assoc_count || 0) + ')</label>';
      }).join('');
    } else {
      concList.innerHTML = '<span class="filter-empty">Nenhum conceito criado</span>';
    }

    document.querySelectorAll('.collection-filter, .concept-filter, #filtro-media-type').forEach(function(el) {
      el.addEventListener('change', function() { invalidateCache(); });
    });
  } catch (err) {
    document.getElementById('status-badge').innerHTML = '<i></i>offline';
  }
}

// ── Floating selection panel ─────────────────────────────────────────────

window.addEventListener('iris:selection-changed', function() {
  var n = window.__irisSelection.size;
  var panel = document.getElementById('floating-panel');
  var summary = document.getElementById('selection-summary');
  var empty = document.getElementById('selection-empty');
  summary.style.display = n ? 'flex' : 'none';
  empty.style.display = n ? 'none' : 'block';
  document.getElementById('selection-count').textContent = n;
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

async function resolveSelectedDbIds() {
  var indices = Array.from(window.__irisSelection.keys());
  var records = await Promise.all(indices.map(function(index) {
    return fetch('/api/records/' + index).then(function(response) {
      if (!response.ok) throw new Error('Item ' + index + ' nao encontrado');
      return response.json();
    });
  }));
  return records.map(function(record) { return record.db_id; }).filter(Boolean);
}

document.getElementById('btn-trash-selected').addEventListener('click', async function() {
  var count = window.__irisSelection.size;
  if (!count) return;
  if (!confirm('Mover ' + count + ' item(ns) para lixeira?')) return;
  try {
    var ids = await resolveSelectedDbIds();
    var result = await trashRecords(ids);
    toast('Movidos: ' + result.moved + ', Falhas: ' + result.failed, result.failed ? 'error' : 'success');
    window.__irisSelection.clear();
    window.dispatchEvent(new CustomEvent('iris:selection-changed'));
    window.location.reload();
  } catch (err) {
    toast('Erro: ' + err.message, 'error');
  }
});

document.getElementById('btn-collection-selected').addEventListener('click', async function() {
  if (!window.__irisSelection.size) return;
  try {
    var data = await listCollections();
    var collections = data.collections || [];
    if (!collections.length) {
      toast('Crie uma colecao antes de adicionar itens', 'info');
      return;
    }
    var menu = collections.map(function(collection, index) {
      return (index + 1) + '. ' + collection.name;
    }).join('\n');
    var answer = prompt('Escolha a colecao:\n\n' + menu);
    var position = parseInt(answer, 10) - 1;
    if (!answer || position < 0 || position >= collections.length) return;
    var ids = await resolveSelectedDbIds();
    await addCollectionMembers(collections[position].id, ids);
    toast(ids.length + ' item(ns) adicionados a ' + collections[position].name, 'success');
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

document.getElementById('btn-search-random').addEventListener('click', function() {
  doRandomSearch(parseInt(document.getElementById('search-topk').value) || 50);
});

var sidebarToggle = document.getElementById('sidebar-toggle');
var sidebarScrim = document.getElementById('sidebar-scrim');

function setSidebarOpen(open) {
  document.body.classList.toggle('sidebar-open', open);
  sidebarToggle.setAttribute('aria-expanded', String(open));
}

sidebarToggle.addEventListener('click', function() {
  setSidebarOpen(!document.body.classList.contains('sidebar-open'));
});

sidebarScrim.addEventListener('click', function() {
  setSidebarOpen(false);
});

document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') setSidebarOpen(false);
});

// ── Video volume sync ───────────────────────────────────────────────────

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
    var info = await fetchInfo();
    var counts = info.extension_counts || {};
    var sorted = Object.entries(counts).sort(function(a, b) { return b[1] - a[1]; });
    var maxCount = sorted.length ? sorted[0][1] : 1;
    var html = '<p style="font-size:11px;margin-bottom:4px;">Extensoes · ' + info.total_records + ' itens</p>';
    if (info.missing_count) {
      html += '<p class="danger-text" style="font-size:10px;margin-bottom:6px;">'
        + info.missing_count + ' arquivo(s) ausente(s)</p>';
    }
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

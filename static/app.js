/* ── Iris App controller ───────────────────────────────────────────────────
   Tab routing, sidebar filters, selection state, floating panel. */

import {
  addCollectionMembers,
  applyEnrichmentSuggestion,
  createEnrichmentJob,
  escapeHtml,
  fetchInfo,
  getEnrichmentJob,
  listEnrichmentSuggestions,
  listCollections,
  listConcepts,
  mediaUrl,
  openFolder,
  rejectEnrichmentSuggestion,
  trashRecords
} from './api.js?v=33';
import { initGallery, invalidateCache } from './gallery.js?v=27';
import { initSearch, doSimilarSearch, doRandomSearch } from './search.js?v=27';
import { initCollections } from './collections.js?v=27';
import { initConcepts } from './concepts.js?v=27';
import { initDuplicates } from './duplicates.js?v=27';
import { initSystem } from './system.js?v=27';

window.__irisSelection = window.__irisSelection || new Map();

// ── Full-screen image viewer ─────────────────────────────────────────────

var lightbox = document.getElementById('image-lightbox');
var lightboxImage = document.getElementById('image-lightbox-image');
var lightboxTitle = document.getElementById('image-lightbox-title');
var lightboxOriginal = document.getElementById('image-lightbox-original');

function openImageLightbox(src, title) {
  if (!src) return;
  lightboxImage.src = src;
  lightboxImage.alt = title || 'Imagem ampliada';
  lightboxTitle.textContent = title || '';
  lightboxOriginal.href = src;
  lightbox.classList.add('open');
  lightbox.setAttribute('aria-hidden', 'false');
  document.body.classList.add('lightbox-open');
  document.getElementById('image-lightbox-close').focus();
}

function closeImageLightbox() {
  lightbox.classList.remove('open');
  lightbox.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('lightbox-open');
  lightboxImage.removeAttribute('src');
}

document.addEventListener('click', function(event) {
  var image = event.target.closest('img[data-lightbox-src]');
  if (image) {
    event.preventDefault();
    event.stopPropagation();
    openImageLightbox(image.dataset.lightboxSrc, image.dataset.lightboxTitle || image.alt);
  }
});

document.getElementById('image-lightbox-close').addEventListener('click', closeImageLightbox);
lightbox.addEventListener('click', function(event) {
  if (event.target === lightbox || event.target.classList.contains('image-lightbox-stage')) {
    closeImageLightbox();
  }
});

document.addEventListener('click', async function(event) {
  var button = event.target.closest('button[data-open-folder]');
  if (!button) return;
  event.preventDefault();
  button.disabled = true;
  try {
    await openFolder(button.dataset.openFolder);
    toast('Pasta aberta', 'success');
  } catch (err) {
    toast('Erro ao abrir pasta: ' + err.message, 'error');
  } finally {
    button.disabled = false;
  }
});

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
    if (r.thumbnail_url) {
      var detailLightbox = !isVideo && hasFile
        ? ' data-lightbox-src="' + escapeHtml(mediaUrl(r.resolved_path)) + '" data-lightbox-title="' + escapeHtml(r.arquivo || '') + '"'
        : '';
      html += '<img src="' + escapeHtml(r.thumbnail_url) + '"' + detailLightbox + ' style="width:260px;max-width:100%;border-radius:8px;flex-shrink:0;">';
    }
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
      html += '<button type="button" class="btn" data-open-folder="' + escapeHtml(r.resolved_path) + '" style="font-size:12px;padding:6px 12px;">📁 Abrir pasta</button>';
      html += '<a href="' + escapeHtml(mediaUrl(r.resolved_path)) + '" class="btn" style="font-size:12px;padding:6px 12px;" target="_blank">📄 Abrir arquivo</a>';
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

document.getElementById('btn-enrich-selected').addEventListener('click', async function() {
  if (!window.__irisSelection.size) return;
  try {
    var ids = await resolveSelectedDbIds();
    await runWebEnrichment(ids, false, false);
  } catch (err) {
    showWebEnrichmentPanel();
    document.getElementById('web-enrichment-status').textContent = 'Erro: ' + err.message;
    toast('Erro: ' + err.message, 'error');
  }
});

function getEnrichBackendConfig() {
  var temp = document.getElementById('we-temporary');
  return {
    backend: (document.getElementById('we-backend') || {}).value || '',
    model: (document.getElementById('we-model') || {}).value || '',
    cdp: (document.getElementById('we-cdp') || {}).value || '',
    temporary: temp ? temp.checked : true,
  };
}

function syncEnrichBackendFields() {
  var backend = (document.getElementById('we-backend') || {}).value || '';
  var show = function(id, on) {
    var el = document.getElementById(id);
    if (el) el.hidden = !on;
  };
  show('we-model-wrap', backend === 'openai' || backend === 'gemini');
  show('we-cdp-wrap', backend === 'webchat');
  show('we-temporary-wrap', backend === 'webchat');
}

function restoreEnrichBackendConfig() {
  try {
    var saved = JSON.parse(localStorage.getItem('irisEnrichBackend') || '{}');
    ['backend', 'model', 'cdp'].forEach(function(k) {
      var el = document.getElementById('we-' + k);
      if (el && saved[k] != null) el.value = saved[k];
    });
    var temp = document.getElementById('we-temporary');
    if (temp && saved.temporary != null) temp.checked = !!saved.temporary;
  } catch (e) { /* ignore */ }
  syncEnrichBackendFields();
}

['we-backend', 'we-model', 'we-cdp', 'we-temporary'].forEach(function(id) {
  var el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('change', function() {
    syncEnrichBackendFields();
    localStorage.setItem('irisEnrichBackend', JSON.stringify(getEnrichBackendConfig()));
  });
});
restoreEnrichBackendConfig();

function backendLabel() {
  var b = (document.getElementById('we-backend') || {}).value || '';
  return { '': 'a heurística', openai: 'o ChatGPT (API)', gemini: 'o Gemini (API)',
           webchat: 'o ChatGPT (web-chat)' }[b] || 'a IA';
}

// force: ignora o cache; research: refaz a busca no Lens (false = reaproveita fontes).
async function runWebEnrichment(ids, force, research) {
  window.__irisLastEnrichIds = ids;
  var job = await createEnrichmentJob(ids, force, getEnrichBackendConfig(), research);
  showWebEnrichmentPanel();
  if (!force && job.cached > 0) {
    toast(job.cached + ' de ' + job.total
      + ' ja tinham sugestao e foram reaproveitadas (sem nova busca).', 'info');
  } else if (research) {
    toast('Re-buscando no Lens ' + job.total + ' item(ns)', 'success');
  } else if (force) {
    toast('Re-enviando ' + job.total + ' item(ns) para ' + backendLabel(), 'success');
  } else {
    toast('Busca web iniciada para ' + job.total + ' item(ns)', 'success');
  }
  // Once there are results, both re-run actions make sense.
  var hasRun = force || job.total > 0;
  document.getElementById('web-enrichment-redistill').hidden = !hasRun;
  document.getElementById('web-enrichment-research').hidden = !hasRun;
  pollWebEnrichmentJob(job.job_id);
}

function showWebEnrichmentPanel() {
  var panel = document.getElementById('web-enrichment-panel');
  panel.hidden = false;
  loadWebEnrichmentSuggestions();
}

// Reaproveita as fontes já encontradas e só re-roda a IA (sem nova busca no Lens).
document.getElementById('web-enrichment-redistill').addEventListener('click', function() {
  var ids = window.__irisLastEnrichIds || [];
  if (!ids.length) return;
  if (!confirm('Re-enviar ' + ids.length + ' imagem(ns) para ' + backendLabel()
    + ' usando as fontes já encontradas (sem nova busca no Lens)?')) return;
  runWebEnrichment(ids, true, false).catch(function(err) {
    toast('Erro: ' + err.message, 'error');
  });
});

// Refaz a busca no Lens do zero (pode abrir o navegador, demorar e pedir CAPTCHA).
document.getElementById('web-enrichment-research').addEventListener('click', function() {
  var ids = window.__irisLastEnrichIds || [];
  if (!ids.length) return;
  if (!confirm('Re-buscar ' + ids.length + ' imagem(ns) no Google Lens do zero? '
    + 'Pode abrir o navegador, demorar e (no modo local) pedir CAPTCHA.')) return;
  runWebEnrichment(ids, true, true).catch(function(err) {
    toast('Erro: ' + err.message, 'error');
  });
});

document.getElementById('web-enrichment-close').addEventListener('click', function() {
  document.getElementById('web-enrichment-panel').hidden = true;
});

// Live status: keeps a per-step elapsed counter ticking every second (the
// browser is hidden, so a long step must still *look* alive and not errored).
var __weTick = null;
var __weJob = null;
var __weStepStart = 0;
var __weLastMsg = '';

function renderWeStatus() {
  var el = document.getElementById('web-enrichment-status');
  var job = __weJob;
  if (!el || !job) return;
  el.classList.remove('we-error');
  if (job.status === 'failed') {
    el.classList.add('we-error');
    el.textContent = '✖ Falhou: ' + (job.error_message || job.message || 'erro desconhecido');
    return;
  }
  if (job.status === 'completed') {
    el.textContent = '✓ Concluído · ' + job.done + '/' + job.total;
    return;
  }
  var secs = Math.round((Date.now() - __weStepStart) / 1000);
  var dots = '.'.repeat((Math.floor(Date.now() / 500) % 3) + 1);
  var line = '⏳ ' + (job.message || job.status) + dots
    + ' · ' + job.done + '/' + job.total + ' · ' + secs + 's';
  if (secs >= 20) {
    line += ' — se pedir login/CAPTCHA, confira a janela do navegador';
  }
  el.textContent = line;
}

function stopWeHeartbeat() {
  if (__weTick) { clearInterval(__weTick); __weTick = null; }
}

async function pollWebEnrichmentJob(jobId) {
  if (!__weTick) {
    __weStepStart = Date.now();
    __weLastMsg = '';
    __weTick = setInterval(renderWeStatus, 500);  // ticks even between polls
  }
  try {
    var job = await getEnrichmentJob(jobId);
    if (job.message !== __weLastMsg || (job.done || 0) !== ((__weJob || {}).done || 0)) {
      __weStepStart = Date.now();  // a new step started -> reset the step timer
      __weLastMsg = job.message;
    }
    __weJob = job;
    renderWeStatus();
    await loadWebEnrichmentSuggestions();
    if (job.status === 'queued' || job.status === 'running') {
      setTimeout(function() { pollWebEnrichmentJob(jobId); }, 1400);
    } else {
      stopWeHeartbeat();
      if (job.status === 'completed') {
        toast('Enriquecimento concluido', 'success');
      } else if (job.status === 'failed') {
        toast('Erro no enriquecimento: ' + (job.error_message || job.message), 'error');
      }
    }
  } catch (err) {
    // A failed poll is not a job failure -- keep retrying, but show we know.
    var el = document.getElementById('web-enrichment-status');
    if (el) el.textContent = '⚠ sem resposta do servidor, tentando de novo... (' + err.message + ')';
    setTimeout(function() { pollWebEnrichmentJob(jobId); }, 2500);
  }
}

async function loadWebEnrichmentSuggestions() {
  var list = document.getElementById('web-enrichment-list');
  try {
    var data = await listEnrichmentSuggestions('pending');
    var suggestions = data.suggestions || [];
    if (!suggestions.length) {
      list.innerHTML = '<p class="filter-empty">Nenhuma sugestao pendente.</p>';
      return;
    }
    list.innerHTML = suggestions.map(renderWebEnrichmentSuggestion).join('');
  } catch (err) {
    list.innerHTML = '<p style="color:var(--accent);">Erro: ' + escapeHtml(err.message) + '</p>';
  }
}

function renderWebEnrichmentSuggestion(item) {
  var sources = (item.sources || []).slice(0, 5).map(function(source) {
    var href = source.source_url || source.url;
    var label = source.title || source.domain || href;
    var sourceLabel = href
      ? '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener">' + escapeHtml(label || 'fonte') + '</a>'
      : '<span>' + escapeHtml(label || 'fonte sem link') + '</span>';
    return '<li>' + sourceLabel
      + (source.domain ? ' <span>' + escapeHtml(source.domain) + '</span>' : '')
      + '</li>';
  }).join('');
  var fields = [
    ['character', 'Personagem', item.character],
    ['source_work', 'Serie/obra', item.source_work],
    ['style', 'Estilo', item.style],
    ['meme_archetype', 'Arquétipo', item.meme_archetype],
    ['context', 'Contexto', item.context],
    ['tags', 'Tags', item.tags],
    ['summary', 'Descricao IA', item.summary],
  ].map(function(field) {
    var hasValue = field[2] && String(field[2]).trim();
    return '<label class="web-field' + (hasValue ? '' : ' muted') + '">'
      + '<input type="checkbox" data-field="' + field[0] + '"' + (hasValue ? ' checked' : ' disabled') + '>'
      + '<span><strong>' + field[1] + '</strong>' + escapeHtml(field[2] || 'sem sugestao') + '</span>'
      + '</label>';
  }).join('');
  return '<article class="web-suggestion-card" data-suggestion-id="' + item.id + '">'
    + '<div class="web-suggestion-main">'
    + '<h4>' + escapeHtml(item.arquivo || ('Registro ' + item.meme_id)) + '</h4>'
    + '<p>' + escapeHtml(item.summary || item.error_message || 'Sem resumo.') + '</p>'
    + '<span class="score-badge">conf ' + Number(item.confidence || 0).toFixed(2) + '</span>'
    + '</div>'
    + '<div class="web-fields">' + fields + '</div>'
    + '<details class="web-sources"><summary>Fontes (' + ((item.sources || []).length) + ')</summary><ul>' + sources + '</ul></details>'
    + '<div class="web-actions">'
    + '<button class="btn btn-primary" data-action="apply-web-suggestion">Aplicar marcados</button>'
    + '<button class="btn btn-subtle" data-action="reject-web-suggestion">Rejeitar</button>'
    + '</div>'
    + '</article>';
}

document.addEventListener('click', async function(event) {
  var applyButton = event.target.closest('button[data-action="apply-web-suggestion"]');
  var rejectButton = event.target.closest('button[data-action="reject-web-suggestion"]');
  var button = applyButton || rejectButton;
  if (!button) return;
  var card = button.closest('.web-suggestion-card');
  if (!card) return;
  var id = parseInt(card.dataset.suggestionId);
  button.disabled = true;
  try {
    if (applyButton) {
      var fields = Array.from(card.querySelectorAll('input[data-field]:checked')).map(function(input) {
        return input.dataset.field;
      });
      await applyEnrichmentSuggestion(id, fields);
      toast('Sugestao aplicada', 'success');
      invalidateCache();
      buildSidebar();
    } else {
      await rejectEnrichmentSuggestion(id);
      toast('Sugestao rejeitada', 'info');
    }
    await loadWebEnrichmentSuggestions();
  } catch (err) {
    toast('Erro: ' + err.message, 'error');
  } finally {
    button.disabled = false;
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
  if (event.key !== 'Escape') return;
  if (lightbox.classList.contains('open')) {
    closeImageLightbox();
    return;
  }
  setSidebarOpen(false);
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

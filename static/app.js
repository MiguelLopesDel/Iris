/* ── Iris App controller ───────────────────────────────────────────────────
   Tab routing, sidebar filters, selection state, floating panel. */

import {
  addCollectionMembers,
  applyEnrichmentSuggestion,
  createCollection,
  createEnrichmentJob,
  escapeHtml,
  fetchInfo,
  getEnrichmentJob,
  listEnrichmentSuggestions,
  listCollections,
  listConcepts,
  openFolder,
  rejectEnrichmentSuggestion,
  trashRecords
} from './api.js?v=39';
import { initGallery, invalidateCache, runGallerySimilar, runGalleryRandom, runGalleryFaceSearch, runGalleryPerson, runGalleryFaceByFace, runGalleryFaceByRecord } from './gallery.js?v=35';
import { initCollections } from './collections.js?v=29';
import { initConcepts } from './concepts.js?v=30';
import { initDuplicates } from './duplicates.js?v=29';
import { initSystem } from './system.js?v=32';
import { initPersons } from './persons.js?v=4';
import { initImportReview } from './import-review.js?v=5';
import { toast } from './ui.js?v=1';

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
  // Fall back to the gallery for unknown/removed tabs (e.g. a stale "#search"
  // hash from before the Busca tab was folded in) so we never blank the view.
  if (!document.getElementById('tab-' + name)) name = 'gallery';
  var viewMeta = {
    gallery: {
      kicker: 'Biblioteca',
      title: 'Galeria',
      description: 'Navegue por toda a sua colecao visual.'
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
    persons: {
      kicker: 'Rostos',
      title: 'Pessoas',
      description: 'Encontre e agrupe mídias pela pessoa que aparece nelas.'
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
  if (name === 'gallery') initGallery();
  if (name === 'collections') initCollections();
  if (name === 'concepts') initConcepts();
  if (name === 'persons') initPersons();
  if (name === 'duplicates') initDuplicates();
  if (name === 'system') { initSystem(); initImportReview(); }
  document.getElementById('view-kicker').textContent = meta.kicker;
  document.getElementById('view-title').textContent = meta.title;
  document.getElementById('view-description').textContent = meta.description;
  document.body.classList.remove('sidebar-open');
  document.getElementById('sidebar-toggle').setAttribute('aria-expanded', 'false');
  window.location.hash = name;
}

// ── Custom events ─────────────────────────────────────────────────────────

window.addEventListener('iris:similar', function(e) {
  // Set the gallery into search mode (runGallerySimilar flips searchActive
  // synchronously) before switching, so initGallery won't load page 1 over it.
  runGallerySimilar(e.detail.index);
  switchTab('gallery');
});

window.addEventListener('iris:person', function(e) {
  // Browse all media of a person in the gallery (same search-mode trick).
  runGalleryPerson(e.detail.personId);
  switchTab('gallery');
});

window.addEventListener('iris:face-search', function(e) {
  if (e.detail && e.detail.file) {
    runGalleryFaceSearch(e.detail.file);
    switchTab('gallery');
  }
});

window.addEventListener('iris:face', function(e) {
  // Search by a specific detected face (a gallery item used as reference).
  runGalleryFaceByFace(e.detail.faceId);
  switchTab('gallery');
});

window.addEventListener('iris:face-record', function(e) {
  runGalleryFaceByRecord(e.detail.index);
  switchTab('gallery');
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

var collectionModal = document.getElementById('collection-modal');
var collectionModalState = {
  collections: [],
  dbIds: [],
  busy: false,
};

function setCollectionModalStatus(message, type) {
  var status = document.getElementById('collection-modal-status');
  status.textContent = message || '';
  status.className = 'collection-modal-status' + (type ? ' ' + type : '');
}

function closeCollectionModal() {
  collectionModal.hidden = true;
  collectionModal.setAttribute('aria-hidden', 'true');
  collectionModalState.collections = [];
  collectionModalState.dbIds = [];
  collectionModalState.busy = false;
  document.getElementById('collection-modal-list').innerHTML = '';
  document.getElementById('collection-modal-name').value = '';
  setCollectionModalStatus('');
}

function renderCollectionChoices() {
  var list = document.getElementById('collection-modal-list');
  var collections = collectionModalState.collections;
  if (!collections.length) {
    list.innerHTML = '<p class="filter-empty">Nenhuma coleção ainda. Crie uma abaixo.</p>';
    return;
  }
  list.innerHTML = collections.map(function(collection) {
    return '<button class="collection-choice" type="button" data-collection-id="' + collection.id + '">'
      + '<strong>' + escapeHtml(collection.name) + '</strong>'
      + '<span>' + (collection.count || 0) + ' itens</span>'
      + '</button>';
  }).join('');
}

async function openCollectionModal() {
  var selectedCount = window.__irisSelection.size;
  if (!selectedCount) return;
  collectionModal.hidden = false;
  collectionModal.setAttribute('aria-hidden', 'false');
  document.getElementById('collection-modal-count').textContent = selectedCount;
  document.getElementById('collection-modal-list').innerHTML = '<p class="filter-empty">Carregando coleções...</p>';
  setCollectionModalStatus('Resolvendo itens selecionados...');
  try {
    collectionModalState.dbIds = await resolveSelectedDbIds();
    if (!collectionModalState.dbIds.length) {
      throw new Error('Nenhum item selecionado possui ID no banco.');
    }
    setCollectionModalStatus('Escolha uma coleção ou crie uma nova.');
    var data = await listCollections();
    collectionModalState.collections = data.collections || [];
    renderCollectionChoices();
    document.getElementById('collection-modal-name').focus();
  } catch (err) {
    setCollectionModalStatus('Erro: ' + err.message, 'error');
    toast('Erro: ' + err.message, 'error');
  }
}

async function addSelectionToCollection(collectionId, collectionName) {
  if (collectionModalState.busy) return;
  collectionModalState.busy = true;
  setCollectionModalStatus('Adicionando itens...');
  try {
    var result = await addCollectionMembers(collectionId, collectionModalState.dbIds);
    var added = typeof result.added === 'number' ? result.added : collectionModalState.dbIds.length;
    toast(added + ' item(ns) adicionados a ' + collectionName, 'success');
    window.__irisSelection.clear();
    window.dispatchEvent(new CustomEvent('iris:selection-changed'));
    closeCollectionModal();
    buildSidebar();
  } catch (err) {
    setCollectionModalStatus('Erro: ' + err.message, 'error');
    toast('Erro: ' + err.message, 'error');
    collectionModalState.busy = false;
  }
}

collectionModal.addEventListener('click', function(event) {
  if (event.target === collectionModal || event.target.id === 'collection-modal-close') {
    closeCollectionModal();
    return;
  }
  var choice = event.target.closest('.collection-choice');
  if (!choice) return;
  var collectionId = parseInt(choice.dataset.collectionId, 10);
  var collection = collectionModalState.collections.find(function(item) {
    return item.id === collectionId;
  });
  if (!collection) return;
  addSelectionToCollection(collection.id, collection.name);
});

document.getElementById('collection-modal-create-form').addEventListener('submit', async function(event) {
  event.preventDefault();
  if (collectionModalState.busy) return;
  var input = document.getElementById('collection-modal-name');
  var name = input.value.trim();
  if (!name) {
    setCollectionModalStatus('Informe um nome para a coleção.', 'error');
    input.focus();
    return;
  }
  collectionModalState.busy = true;
  setCollectionModalStatus('Criando coleção...');
  try {
    var created = await createCollection(name);
    var collectionId = created.collection_id;
    if (!collectionId) {
      var data = await listCollections();
      var normalized = name.toLowerCase();
      var match = (data.collections || []).find(function(collection) {
        return String(collection.name || '').toLowerCase() === normalized;
      });
      collectionId = match ? match.id : null;
    }
    if (!collectionId) throw new Error('Coleção criada, mas não consegui identificar o ID.');
    collectionModalState.busy = false;
    await addSelectionToCollection(collectionId, name);
  } catch (err) {
    setCollectionModalStatus('Erro: ' + err.message, 'error');
    toast('Erro: ' + err.message, 'error');
    collectionModalState.busy = false;
  }
});

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
  openCollectionModal();
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
  var warn = item.error_message
    ? '<p class="web-warn">⚠ ' + escapeHtml(item.error_message) + '</p>'
    : '';
  return '<article class="web-suggestion-card" data-suggestion-id="' + item.id + '">'
    + '<div class="web-suggestion-main">'
    + '<h4>' + escapeHtml(item.arquivo || ('Registro ' + item.meme_id)) + '</h4>'
    + warn
    + '<p>' + escapeHtml(item.summary || 'Sem resumo.') + '</p>'
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
  runGalleryRandom(parseInt(document.getElementById('search-topk').value) || 50);
  switchTab('gallery');
});

document.getElementById('btn-refresh').addEventListener('click', function() {
  invalidateCache();
  window.dispatchEvent(new CustomEvent('iris:selection-changed'));
  toast('Dados atualizados', 'info');
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
  if (!collectionModal.hidden) {
    closeCollectionModal();
    return;
  }
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
    // Stats panel is the one place that wants the missing-file count, so it
    // opts into the O(N) scan; the sidebar's fetchInfo() stays syscall-free.
    var info = await fetchInfo({ checkMissing: true });
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

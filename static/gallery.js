/* ── Iris Gallery module ────────────────────────────────────────────────────
   Fast paginated media browser with client-side pre-fetching.
   Arrow ← → switches pages instantly — content is pre-loaded in hidden divs. */

import { debounce, escapeHtml, fetchRecords, getPersonMedia, mediaUrl, searchFace, searchFaceByFace, searchFaceByRecord, searchFilename, searchImage, searchRandom, searchSimilar, searchText } from './api.js?v=46';
import { openDetail } from './detail.js?v=8';

// ── Module state ──────────────────────────────────────────────────────────
let currentPage = 1;
let totalPages = 1;
let totalItems = 0;
let perPage = 24;
let sortBy = 'data';
let sortAsc = 0;
let searchActive = false;

/* Page cache: Map<number, { records, total, total_pages, missing_count }>
   Pre-fetched pages are stored here so arrow clicks hit memory, not network. */
const pageCache = new Map();

/* Selection state is managed by app.js — gallery just renders checkboxes. */
(window.__irisSelection = window.__irisSelection || new Map());

// ── Filter helpers ──────────────────────────────────────────────────────

function getFilterParams() {
  const mediaType = document.getElementById('filtro-media-type')?.value || 'all';
  const collections = [...document.querySelectorAll('.collection-filter:checked')].map(cb => cb.value).join(',');
  const concepts = [...document.querySelectorAll('.concept-filter:checked')].map(cb => cb.value).join(',');
  return { mediaType, collections, concepts };
}

// ── Initialization ────────────────────────────────────────────────────────

export function initGallery() {
  perPage = readPerPage();
  sortBy = document.getElementById('gallery-sort').value;
  sortAsc = document.getElementById('gallery-sort-asc').checked ? 1 : 0;

  document.getElementById('gallery-sort').onchange = () => { sortBy = document.getElementById('gallery-sort').value; invalidateCache(); };
  document.getElementById('gallery-sort-asc').onchange = () => { sortAsc = document.getElementById('gallery-sort-asc').checked ? 1 : 0; invalidateCache(); };
  document.getElementById('gallery-per-page').onchange = () => {
    syncCustomPerPage();
    applyPerPage();
  };
  document.getElementById('gallery-per-page-custom').onchange = applyPerPage;
  document.getElementById('gallery-per-page-custom').onkeydown = (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      applyPerPage();
    }
  };
  document.getElementById('gallery-prev').onclick = () => goToPage(currentPage - 1);
  document.getElementById('gallery-next').onclick = () => goToPage(currentPage + 1);
  document.getElementById('gallery-page-go').onclick = jumpToTypedPage;
  document.getElementById('gallery-page-jump').onkeydown = (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      jumpToTypedPage();
    }
  };
  syncCustomPerPage();
  const searchInput = document.getElementById('gallery-search');
  const imageInput = document.getElementById('gallery-image-search');
  const clearButton = document.getElementById('gallery-clear-search');
  if (searchInput.dataset.initialized !== 'true') {
    searchInput.dataset.initialized = 'true';
    searchInput.addEventListener('input', debounce(() => {
      const query = searchInput.value.trim();
      if (query) runGalleryTextSearch(query);
      else if (searchActive) clearGallerySearch();
    }, 350));
    imageInput.addEventListener('change', () => {
      const file = imageInput.files[0];
      imageInput.value = '';
      if (file) runGalleryImageSearch(file);
    });
    const faceInput = document.getElementById('gallery-face-search');
    if (faceInput) {
      faceInput.addEventListener('change', () => {
        const file = faceInput.files[0];
        faceInput.value = '';
        if (file) runGalleryFaceSearch(file);
      });
    }
    clearButton.addEventListener('click', clearGallerySearch);
    document.getElementById('gallery-context-clear').addEventListener('click', clearGallerySearch);
    document.getElementById('gallery-random').addEventListener('click', () => {
      runGalleryRandom(parseInt(document.getElementById('search-topk')?.value) || 50);
    });
    const advancedBtn = document.getElementById('gallery-search-advanced');
    advancedBtn.addEventListener('click', () => {
      const panel = document.getElementById('gallery-group-controls');
      const open = panel.hidden;
      panel.hidden = !open;
      advancedBtn.setAttribute('aria-expanded', String(open));
    });
    const groupThreshold = document.getElementById('image-group-threshold');
    groupThreshold.addEventListener('input', () => {
      document.getElementById('image-group-threshold-val').textContent =
        Number(groupThreshold.value).toFixed(2);
    });

    // Drag an image onto the search bar — skips the native file dialog entirely.
    const hero = document.querySelector('.gallery-search-hero');
    ['dragover', 'dragenter'].forEach(ev => hero.addEventListener(ev, (e) => {
      e.preventDefault();
      hero.classList.add('drag-over');
    }));
    ['dragleave', 'drop'].forEach(ev => hero.addEventListener(ev, (e) => {
      e.preventDefault();
      hero.classList.remove('drag-over');
    }));
    hero.addEventListener('drop', (e) => {
      const file = [...(e.dataTransfer?.files || [])].find(f => f.type.startsWith('image/'));
      if (file) runGalleryImageSearch(file);
    });

    // Paste an image from the clipboard (Ctrl+V) while the gallery is open.
    document.addEventListener('paste', (e) => {
      if (!document.getElementById('tab-gallery').classList.contains('active')) return;
      const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
      const file = item?.getAsFile();
      if (file) runGalleryImageSearch(file);
    });
  }

  if (!searchActive) loadPage(currentPage);
}

function syncCustomPerPage() {
  const select = document.getElementById('gallery-per-page');
  const customInput = document.getElementById('gallery-per-page-custom');
  const custom = select.value === 'custom';
  customInput.hidden = !custom;
  if (custom) customInput.focus();
}

function readPerPage() {
  const select = document.getElementById('gallery-per-page');
  const customInput = document.getElementById('gallery-per-page-custom');
  const rawValue = select.value === 'custom' ? customInput.value : select.value;
  return Math.min(500, Math.max(12, parseInt(rawValue, 10) || 24));
}

function applyPerPage() {
  perPage = readPerPage();
  if (document.getElementById('gallery-per-page').value === 'custom') {
    document.getElementById('gallery-per-page-custom').value = perPage;
  }
  invalidateCache();
}

function invalidateCache() {
  pageCache.clear();
  currentPage = 1;
  resetGallerySearchState();
  loadPage(1);
}

// ── Page loading + caching ───────────────────────────────────────────────

async function loadPage(page) {
  if (page < 1) return;

  // Check cache
  if (pageCache.has(page)) {
    const cached = pageCache.get(page);
    currentPage = page;
    totalPages = cached.total_pages;
    totalItems = cached.total;
    updatePaginationUI();
    renderGrid(cached.records);
    prefetchAdjacent(page);
    return;
  }

  // Fetch from API
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = renderSkeletons(perPage);

  try {
    const { mediaType, collections, concepts } = getFilterParams();
    const data = await fetchRecords(page, perPage, sortBy, sortAsc, mediaType, collections, concepts);
    pageCache.set(data.page, data);
    currentPage = data.page;
    totalPages = data.total_pages;
    totalItems = data.total;
    updatePaginationUI();
    renderGrid(data.records);
    prefetchAdjacent(data.page);
  } catch (err) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

function prefetchAdjacent(page) {
  const { mediaType, collections, concepts } = getFilterParams();
  for (const p of [page - 1, page + 1]) {
    if (p >= 1 && p <= totalPages && !pageCache.has(p)) {
      fetchRecords(p, perPage, sortBy, sortAsc, mediaType, collections, concepts)
        .then(d => { pageCache.set(d.page, d); })
        .catch(() => {}); // silent
    }
  }
}

function goToPage(page) {
  if (page < 1 || page > totalPages) return;
  loadPage(page);
}

function jumpToTypedPage() {
  const input = document.getElementById('gallery-page-jump');
  const page = Math.min(totalPages, Math.max(1, parseInt(input.value, 10) || 1));
  input.value = page;
  goToPage(page);
}

// ── Render ────────────────────────────────────────────────────────────────

// Records currently rendered in the grid — the detail modal navigates this list.
let currentRecords = [];

function getCurrentRecords() {
  return currentRecords;
}

function renderGrid(records) {
  const grid = document.getElementById('gallery-grid');
  currentRecords = records || [];
  if (!records.length) {
    grid.innerHTML = '<div class="empty-state"><span class="empty-state-icon">⌕</span><p>Nenhum item encontrado.</p><small>Ajuste os termos da busca ou limpe os filtros da barra lateral.</small></div>';
    return;
  }
  // By date, the grid reads like the phone's gallery: a heading per day.
  const byDay = !searchActive && sortBy === 'data';
  let lastDay = null;
  grid.innerHTML = records.map((record) => {
    let heading = '';
    if (byDay) {
      const day = dayLabel(record.file_mtime);
      if (day !== lastDay) {
        heading = `<h3 class="grid-day">${escapeHtml(day)}</h3>`;
        lastDay = day;
      }
    }
    return heading + renderCard(record);
  }).join('');
  observeLazyImages(grid);
}

// "Hoje", "Ontem", "9 de outubro", "9 de outubro de 2024", or "Sem data".
export function dayLabel(epochSeconds, now = new Date()) {
  if (!epochSeconds) return 'Sem data';
  const date = new Date(epochSeconds * 1000);
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(date)) / 86400000);
  if (days === 0) return 'Hoje';
  if (days === 1) return 'Ontem';
  const options = { day: 'numeric', month: 'long' };
  if (date.getFullYear() !== now.getFullYear()) options.year = 'numeric';
  return date.toLocaleDateString('pt-BR', options);
}

// A tile, as in a phone gallery: the picture opens the viewer. Selection and
// the shortcuts to similar photos and to this person show on hover or focus.
function renderCard(record) {
  const sel = window.__irisSelection.has(record.index) ? 'checked' : '';
  const thumb = record.thumbnail_url || '';
  const name = escapeHtml(record.arquivo || '');
  const isVideo = record.media_type === 'video';
  const score = record.score != null
    ? `<span class="score-badge">${Number(record.score).toFixed(3)}</span>`
    : '';
  const picture = thumb
    ? `<img src="${thumb}" loading="lazy" alt="${name}" data-open-detail="${record.index}">`
    : `<button class="tile-missing" type="button" data-action="detail" data-index="${record.index}"
         aria-label="Abrir ${name}"><span aria-hidden="true">${isVideo ? '🎬' : '🖼️'}</span></button>`;
  const video = isVideo
    ? `<button class="play-overlay" type="button" data-index="${record.index}" aria-label="Reproduzir ${name}">▶</button>`
    : '';

  return `<div class="media-card" data-index="${record.index}" title="${name}">
    <div class="media-card-img" id="card-img-${record.index}">
      ${picture}
      ${video}
      ${score}
      <input type="checkbox" class="media-checkbox" id="sel_${record.index}" data-index="${record.index}"
             aria-label="Selecionar ${name}" ${sel}>
      <div class="tile-actions">
        <button class="tile-action" type="button" data-action="similar" data-index="${record.index}"
                title="Fotos parecidas" aria-label="Fotos parecidas com ${name}">≈</button>
        <button class="tile-action" type="button" data-action="face" data-index="${record.index}"
                title="Fotos com esta pessoa" aria-label="Fotos com a pessoa de ${name}">☺</button>
      </div>
    </div>
  </div>`;
}

function gallerySearchOptions() {
  const mode = document.getElementById('search-mode')?.value || 'hybrid';
  let balance = 0.5;
  let text_bonus = 2.0;
  let lexical_weight = 0.25;
  if (mode === 'text') {
    balance = 0.0;
    text_bonus = 3.0;
    lexical_weight = 0.4;
  } else if (mode === 'visual') {
    balance = 0.65;
    text_bonus = 0.5;
    lexical_weight = 0.0;
  } else if (mode === 'custom') {
    balance = parseFloat(document.getElementById('search-balance').value);
    text_bonus = parseFloat(document.getElementById('search-textbonus').value);
    lexical_weight = parseFloat(document.getElementById('search-lexical').value);
  }
  const filters = getFilterParams();
  return {
    top_k: parseInt(document.getElementById('search-topk').value) || 50,
    threshold: parseFloat(document.getElementById('search-threshold').value),
    balance,
    text_bonus,
    lexical_weight,
    translate: document.getElementById('search-translate').checked,
    media_type: filters.mediaType,
    collection_ids: filters.collections,
    concept_ids: filters.concepts,
  };
}

async function runGalleryTextSearch(query) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Buscando...</p>';
  try {
    const semanticSearch = window.__irisCapabilities?.semantic_search !== false;
    const mode = document.getElementById('search-mode')?.value || 'hybrid';
    const data = mode === 'filename' || !semanticSearch
      ? await searchFilename(query, gallerySearchOptions())
      : await searchText(query, gallerySearchOptions());
    document.getElementById('gallery-page-info').textContent = `${data.total} resultados`;
    const kind = semanticSearch ? 'Busca' : 'Nome do arquivo';
    setGalleryContext(`${kind}: <strong>${escapeHtml(query)}</strong> · ${data.total} resultado(s)`);
    renderGrid(data.results);
  } catch (error) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

async function runGalleryImageSearch(file) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Buscando por imagem...</p>';
  try {
    const opts = {
      ...gallerySearchOptions(),
      group_results: document.getElementById('image-group-enabled').checked,
      group_threshold: document.getElementById('image-group-threshold').value,
      show_singletons: document.getElementById('image-group-singletons').checked,
    };
    const data = await searchImage(file, opts);
    document.getElementById('gallery-page-info').textContent = `${data.total} resultados`;
    setGalleryContext(`Busca por imagem · ${data.total} resultado(s)`);
    if (Array.isArray(data.groups)) renderGroupedGrid(data.groups);
    else renderGrid(data.results);
  } catch (error) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

// Busca por pessoa: detecta o rosto da foto enviada e traz mídias com aquela pessoa.
export async function runGalleryFaceSearch(file) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Procurando a pessoa...</p>';
  try {
    const data = await searchFace(file, { top_k: parseInt(document.getElementById('search-topk')?.value) || 50 });
    document.getElementById('gallery-page-info').textContent = `${data.total} com essa pessoa`;
    setGalleryContext(`Pessoa da foto enviada · ${data.total} mídia(s)`);
    renderGrid(data.results);
  } catch (error) {
    const friendly = /422/.test(error.message)
      ? 'Nenhum rosto detectado na imagem enviada. Tente uma foto mais nítida e de frente.'
      : ('Erro: ' + error.message);
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">${escapeHtml(friendly)}</p>`;
  }
}

// Busca por pessoa usando um item JÁ na galeria como referência (rosto mais forte).
export async function runGalleryFaceByRecord(index) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Procurando essa pessoa na biblioteca...</p>';
  try {
    const data = await searchFaceByRecord(index, { top_k: parseInt(document.getElementById('search-topk')?.value) || 50 });
    document.getElementById('gallery-page-info').textContent = `${data.total} com essa pessoa`;
    setGalleryContext(`Mídias com a pessoa desta mídia · ${data.total} item(ns)`);
    renderGrid(data.results);
  } catch (error) {
    const friendly = /422/.test(error.message)
      ? 'Nenhum rosto detectado nesta mídia — não dá para buscar por pessoa a partir dela.'
      : ('Erro: ' + error.message);
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">${escapeHtml(friendly)}</p>`;
  }
}

// Busca por pessoa a partir de um rosto específico (chip no painel de detalhe).
export async function runGalleryFaceByFace(faceId) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Procurando essa pessoa...</p>';
  try {
    const data = await searchFaceByFace(faceId, { top_k: parseInt(document.getElementById('search-topk')?.value) || 50 });
    document.getElementById('gallery-page-info').textContent = `${data.total} com essa pessoa`;
    setGalleryContext(`Mídias com o rosto selecionado · ${data.total} item(ns)`);
    renderGrid(data.results);
  } catch (error) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

// Navega todas as mídias de uma pessoa (clusters da aba Pessoas / rosto no detalhe).
export async function runGalleryPerson(personId) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Carregando mídias da pessoa...</p>';
  try {
    const data = await getPersonMedia(personId);
    document.getElementById('gallery-page-info').textContent = `${data.total} dessa pessoa`;
    setGalleryContext(`Pessoa: <strong>${escapeHtml(data.person_name || 'Sem nome')}</strong> · ${data.total} mídia(s)`);
    renderGrid(data.results);
  } catch (error) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

// Renders a similar / random query into the gallery grid. Sets searchActive
// synchronously so a concurrent switchTab → initGallery won't trigger a page load.
export async function runGallerySimilar(index) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Buscando similares...</p>';
  try {
    const data = await searchSimilar(index, gallerySearchOptions());
    document.getElementById('gallery-page-info').textContent = `${data.total} similares`;
    setGalleryContext(`Similares · ${data.total} item(ns)`);
    renderGrid(data.results);
  } catch (error) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

export async function runGalleryRandom(n = 50) {
  enterSearchMode();
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Carregando aleatórios...</p>';
  try {
    const data = await searchRandom(n);
    document.getElementById('gallery-page-info').textContent = `${data.total} aleatórios`;
    setGalleryContext(`Exploração aleatória · ${data.total} item(ns)`);
    renderGrid(data.results);
  } catch (error) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

function renderGroupedGrid(groups) {
  const grid = document.getElementById('gallery-grid');
  currentRecords = groups.flat();
  if (!groups.length) {
    grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Nenhum grupo encontrado.</p>';
    return;
  }
  grid.innerHTML = groups.map((group, i) => `
    <section class="search-result-group">
      <div class="search-result-group-title">Grupo ${i + 1} · ${group.length} item(ns)</div>
      <div class="media-grid">${group.map(renderCard).join('')}</div>
    </section>
  `).join('');
  observeLazyImages(grid);
}

function clearGallerySearch() {
  resetGallerySearchState();
  loadPage(1);
}

// Controls that only make sense while browsing the paginated library. A search
// result is a flat top-k list, so during a search these are disabled — otherwise
// a stray click (next page / per-page / sort) silently wipes out the results.
const BROWSE_CONTROL_IDS = [
  'gallery-prev', 'gallery-next', 'gallery-page-go', 'gallery-page-jump',
  'gallery-per-page', 'gallery-per-page-custom', 'gallery-sort', 'gallery-sort-asc',
];

function setBrowseControlsDisabled(disabled) {
  for (const id of BROWSE_CONTROL_IDS) {
    const el = document.getElementById(id);
    if (el) el.disabled = disabled;
  }
}

function enterSearchMode() {
  searchActive = true;
  renderBottomPager();
  document.getElementById('gallery-clear-search').hidden = false;
  setBrowseControlsDisabled(true);
}

// Context bar: tells the user WHAT the grid is currently showing (search
// results, a person's media, similars…) and offers a one-click way back.
function setGalleryContext(html) {
  const bar = document.getElementById('gallery-context');
  const label = document.getElementById('gallery-context-label');
  if (!bar || !label) return;
  label.innerHTML = html;
  bar.hidden = false;
}

function clearGalleryContext() {
  const bar = document.getElementById('gallery-context');
  if (bar) bar.hidden = true;
}

function resetGallerySearchState() {
  searchActive = false;
  document.getElementById('gallery-search').value = '';
  document.getElementById('gallery-image-search').value = '';
  document.getElementById('gallery-clear-search').hidden = true;
  clearGalleryContext();
  setBrowseControlsDisabled(false);
}

function renderSkeletons(n) {
  let html = '';
  for (let i = 0; i < n; i++) {
    html += `<div class="skeleton" style="aspect-ratio:1;"></div>`;
  }
  return html;
}

// Page numbers to offer: the first, the last, and a window around the
// current one; gaps become an ellipsis. 1 … 4 5 [6] 7 8 … 20
export function pageWindow(current, total, radius = 2) {
  const pages = new Set([1, total]);
  for (let p = current - radius; p <= current + radius; p++) {
    if (p >= 1 && p <= total) pages.add(p);
  }
  const sorted = [...pages].sort((a, b) => a - b);
  const items = [];
  sorted.forEach((page, i) => {
    if (i && page - sorted[i - 1] > 1) items.push('…');
    items.push(page);
  });
  return items;
}

// The pager under the grid: where the eye is after scrolling through a page.
function renderBottomPager() {
  const pager = document.getElementById('gallery-pagination-bottom');
  if (!pager) return;
  if (searchActive || totalPages <= 1) {
    pager.innerHTML = '';
    pager.hidden = true;
    return;
  }
  pager.hidden = false;
  const numbers = pageWindow(currentPage, totalPages).map((item) => (
    item === '…'
      ? '<span class="pager-gap" aria-hidden="true">…</span>'
      : `<button type="button" class="pager-page${item === currentPage ? ' is-current' : ''}"
           data-page="${item}" ${item === currentPage ? 'aria-current="page"' : ''}
           aria-label="Página ${item}">${item}</button>`
  )).join('');
  pager.innerHTML = `
    <nav class="pager" aria-label="Páginas da galeria">
      <button type="button" class="btn pager-step" data-page="${currentPage - 1}"
              ${currentPage <= 1 ? 'disabled' : ''}>← Anterior</button>
      <div class="pager-pages">${numbers}</div>
      <button type="button" class="btn pager-step" data-page="${currentPage + 1}"
              ${currentPage >= totalPages ? 'disabled' : ''}>Próxima →</button>
    </nav>
    <p class="pager-info">Página ${currentPage} de ${totalPages}</p>`;
  pager.querySelectorAll('[data-page]').forEach((button) => {
    button.onclick = () => {
      goToPage(parseInt(button.dataset.page, 10));
      document.getElementById('gallery-grid')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  });
}

function updatePaginationUI() {
  renderBottomPager();
  const count = `${totalItems.toLocaleString('pt-BR')} ${totalItems === 1 ? 'item' : 'itens'}`;
  document.getElementById('gallery-page-info').textContent =
    totalPages > 1 ? `${count} · página ${currentPage} de ${totalPages}` : count;
  document.getElementById('gallery-prev').disabled = currentPage <= 1;
  document.getElementById('gallery-next').disabled = currentPage >= totalPages;
  const pageInput = document.getElementById('gallery-page-jump');
  pageInput.max = totalPages;
  pageInput.value = currentPage;
}

// ── Lazy image loading ───────────────────────────────────────────────────

function observeLazyImages(container) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const img = entry.target;
        if (img.dataset.src) {
          img.src = img.dataset.src;
          img.removeAttribute('data-src');
        }
        observer.unobserve(img);
      }
    });
  }, { rootMargin: '200px' });

  container.querySelectorAll('img[loading="lazy"]').forEach(img => observer.observe(img));
}

// ── Event delegation (video play, similar, detail, checkbox) ──────────────

function openDetailAt(index) {
  const pos = currentRecords.findIndex((r) => r.index === index);
  if (pos >= 0) openDetail(currentRecords, pos);
  else openDetail([{ index }], 0);
}

document.addEventListener('click', (e) => {
  // Clicking the card image opens the detail modal (replaces the lightbox here).
  const img = e.target.closest('img[data-open-detail]');
  if (img) {
    e.preventDefault();
    openDetailAt(parseInt(img.dataset.openDetail));
    return;
  }

  // Person chip on a card → browse all media of that person.
  const chip = e.target.closest('[data-person-chip]');
  if (chip) {
    window.dispatchEvent(new CustomEvent('iris:person', { detail: { personId: parseInt(chip.dataset.personChip) } }));
    return;
  }

  const btn = e.target.closest('button');
  if (!btn) return;

  const index = parseInt(btn.dataset.index);
  if (isNaN(index)) return;

  // ▶ Video: open the detail modal, which plays it in the stage
  if (btn.classList.contains('play-overlay')) {
    openDetailAt(index);
    return;
  }

  // Similar search
  if (btn.dataset.action === 'similar') {
    window.dispatchEvent(new CustomEvent('iris:similar', { detail: { index } }));
    return;
  }

  // Search by person from this gallery item
  if (btn.dataset.action === 'face') {
    runGalleryFaceByRecord(index);
    return;
  }

  // Detail modal
  if (btn.dataset.action === 'detail') {
    openDetailAt(index);
    return;
  }

  // Selection checkbox
  if (btn.classList.contains('media-checkbox')) {
    const idx = parseInt(btn.dataset.index);
    if (btn.checked) window.__irisSelection.set(idx, true);
    else window.__irisSelection.delete(idx);
    window.dispatchEvent(new CustomEvent('iris:selection-changed'));
  }
});

// Checkbox change listener
document.addEventListener('change', (e) => {
  if (!e.target.matches('.media-checkbox')) return;
  const idx = parseInt(e.target.dataset.index);
  if (isNaN(idx)) return;
  if (e.target.checked) window.__irisSelection.set(idx, true);
  else window.__irisSelection.delete(idx);
  window.dispatchEvent(new CustomEvent('iris:selection-changed'));
});

// ── Keyboard navigation ──────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  // Only when gallery tab is active and the detail modal is not capturing keys
  const galleryPane = document.getElementById('tab-gallery');
  if (!galleryPane.classList.contains('active')) return;
  if (document.body.classList.contains('detail-open')) return;
  if (e.target.matches('input, textarea, select, [contenteditable="true"]')) return;

  if (e.key === 'ArrowLeft') { e.preventDefault(); goToPage(currentPage - 1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); goToPage(currentPage + 1); }
});

// ── Export for app.js ─────────────────────────────────────────────────────

export { loadPage, goToPage, renderGrid, invalidateCache, currentPage, totalPages, getCurrentRecords };

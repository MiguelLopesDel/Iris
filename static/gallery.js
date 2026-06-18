/* ── Iris Gallery module ────────────────────────────────────────────────────
   Fast paginated media browser with client-side pre-fetching.
   Arrow ← → switches pages instantly — content is pre-loaded in hidden divs. */

import { debounce, escapeHtml, fetchRecords, mediaUrl, searchImage, searchRandom, searchSimilar, searchText } from './api.js?v=35';

// ── Module state ──────────────────────────────────────────────────────────
let currentPage = 1;
let totalPages = 1;
let perPage = 24;
let sortBy = 'importacao';
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
    clearButton.addEventListener('click', clearGallerySearch);
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

function renderGrid(records) {
  const grid = document.getElementById('gallery-grid');
  if (!records.length) {
    grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Nenhum item encontrado.</p>';
    return;
  }
  grid.innerHTML = records.map(r => renderCard(r)).join('');
  observeLazyImages(grid);
}

function renderCard(record) {
  const sel = window.__irisSelection.has(record.index) ? 'checked' : '';
  const thumb = record.thumbnail_url || '';
  const name = escapeHtml(record.arquivo).slice(0, 50);
  const score = record.score != null
    ? `<span class="score-badge">${Number(record.score).toFixed(3)}</span>`
    : '';

  if (!thumb) {
    return `<div class="media-card" data-index="${record.index}">
      <div class="placeholder-card">
        <span class="icon">${record.media_type === 'video' ? '🎬' : '🖼️'}</span>
        <span>indisponivel</span>
      </div>
      <div class="media-card-body">
        <div class="caption" title="${escapeHtml(record.arquivo)}">${name}</div>
        <div class="actions">
          <button class="btn" data-action="similar" data-index="${record.index}">Similares</button>
          <button class="btn" data-action="detail" data-index="${record.index}">Detalhes</button>
        </div>
      </div>
    </div>`;
  }

  const fullSrc = record.media_type === 'image' ? mediaUrl(record.resolved_path) : '';
  const lightboxAttrs = fullSrc
    ? ` data-lightbox-src="${escapeHtml(fullSrc)}" data-lightbox-title="${escapeHtml(record.arquivo)}"`
    : '';
  const imgTag = `<img src="${thumb}" loading="lazy" alt="${name}"${lightboxAttrs}>`;
  const playBtn = record.media_type === 'video'
    ? `<button class="play-overlay" data-index="${record.index}" data-path="${escapeHtml(record.resolved_path || '')}">▶</button>`
    : '';

  return `<div class="media-card" data-index="${record.index}">
    <div class="media-card-img" id="card-img-${record.index}">
      ${imgTag}
      ${playBtn}
      ${score}
      <input type="checkbox" class="media-checkbox" id="sel_${record.index}" data-index="${record.index}" ${sel}>
    </div>
    <div class="media-card-body">
      <div class="caption" title="${escapeHtml(record.arquivo)}">${name}</div>
      <div class="actions">
        <button class="btn" data-action="similar" data-index="${record.index}">Similares</button>
        <button class="btn" data-action="detail" data-index="${record.index}">Detalhes</button>
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
    const data = await searchText(query, gallerySearchOptions());
    document.getElementById('gallery-page-info').textContent = `${data.total} resultados`;
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
    if (Array.isArray(data.groups)) renderGroupedGrid(data.groups);
    else renderGrid(data.results);
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
    renderGrid(data.results);
  } catch (error) {
    grid.innerHTML = `<p style="color:var(--accent);padding:20px;">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

function renderGroupedGrid(groups) {
  const grid = document.getElementById('gallery-grid');
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
  document.getElementById('gallery-clear-search').hidden = false;
  setBrowseControlsDisabled(true);
}

function resetGallerySearchState() {
  searchActive = false;
  document.getElementById('gallery-search').value = '';
  document.getElementById('gallery-image-search').value = '';
  document.getElementById('gallery-clear-search').hidden = true;
  setBrowseControlsDisabled(false);
}

function renderSkeletons(n) {
  let html = '';
  for (let i = 0; i < n; i++) {
    html += `<div class="skeleton" style="aspect-ratio:1;"></div>`;
  }
  return html;
}

function updatePaginationUI() {
  document.getElementById('gallery-page-info').textContent =
    `Pag ${currentPage} / ${totalPages}`;
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

document.addEventListener('click', (e) => {
  const btn = e.target.closest('button');
  if (!btn) return;

  const index = parseInt(btn.dataset.index);
  if (isNaN(index)) return;

  // ▶ Play video
  if (btn.classList.contains('play-overlay')) {
    const path = btn.dataset.path;
    if (!path) return;
    const container = document.getElementById(`card-img-${index}`);
    container.innerHTML = `<video src="/media/${path}" controls autoplay style="width:100%;height:100%;object-fit:contain;background:#000;"></video>`;
    return;
  }

  // Similar search
  if (btn.dataset.action === 'similar') {
    window.dispatchEvent(new CustomEvent('iris:similar', { detail: { index } }));
    return;
  }

  // Detail panel
  if (btn.dataset.action === 'detail') {
    window.dispatchEvent(new CustomEvent('iris:detail', { detail: { index } }));
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
  // Only when gallery tab is active
  const galleryPane = document.getElementById('tab-gallery');
  if (!galleryPane.classList.contains('active')) return;
  if (e.target.matches('input, textarea, select, [contenteditable="true"]')) return;

  if (e.key === 'ArrowLeft') { e.preventDefault(); goToPage(currentPage - 1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); goToPage(currentPage + 1); }
});

// ── Export for app.js ─────────────────────────────────────────────────────

export { loadPage, goToPage, renderGrid, invalidateCache, currentPage, totalPages };

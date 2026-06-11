/* ── Iris Gallery module ────────────────────────────────────────────────────
   Fast paginated media browser with client-side pre-fetching.
   Arrow ← → switches pages instantly — content is pre-loaded in hidden divs. */

import { fetchRecords, escapeHtml } from './api.js';

// ── Module state ──────────────────────────────────────────────────────────
let currentPage = 1;
let totalPages = 1;
let perPage = 24;
let sortBy = 'importacao';
let sortAsc = 0;

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
  perPage = parseInt(document.getElementById('gallery-per-page').value) || 24;
  sortBy = document.getElementById('gallery-sort').value;
  sortAsc = document.getElementById('gallery-sort-asc').checked ? 1 : 0;

  document.getElementById('gallery-sort').onchange = () => { sortBy = document.getElementById('gallery-sort').value; invalidateCache(); };
  document.getElementById('gallery-sort-asc').onchange = () => { sortAsc = document.getElementById('gallery-sort-asc').checked ? 1 : 0; invalidateCache(); };
  document.getElementById('gallery-per-page').onchange = () => { perPage = parseInt(document.getElementById('gallery-per-page').value); invalidateCache(); };
  document.getElementById('gallery-prev').onclick = () => goToPage(currentPage - 1);
  document.getElementById('gallery-next').onclick = () => goToPage(currentPage + 1);

  loadPage(currentPage);
}

function invalidateCache() {
  pageCache.clear();
  currentPage = 1;
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

  const imgTag = `<img src="${thumb}" loading="lazy" alt="${name}">`;
  const playBtn = record.media_type === 'video'
    ? `<button class="play-overlay" data-index="${record.index}" data-path="${escapeHtml(record.resolved_path || '')}">▶</button>`
    : '';

  return `<div class="media-card" data-index="${record.index}">
    <div class="media-card-img" id="card-img-${record.index}">
      ${imgTag}
      ${playBtn}
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

  if (e.key === 'ArrowLeft') { e.preventDefault(); goToPage(currentPage - 1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); goToPage(currentPage + 1); }
});

// ── Export for app.js ─────────────────────────────────────────────────────

export { loadPage, goToPage, renderGrid, invalidateCache, currentPage, totalPages };

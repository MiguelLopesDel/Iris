/* ── Iris App controller ───────────────────────────────────────────────────
   Tab routing, sidebar filters, selection state, floating panel. */

import { fetchInfo, listCollections, listConcepts, trashRecords } from './api.js';
import { initGallery } from './gallery.js';
import { initSearch, doSimilarSearch, doRandomSearch } from './search.js';

// ── Global selection state ───────────────────────────────────────────────
window.__irisSelection = window.__irisSelection || new Map();

// ── Tab routing ──────────────────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`[data-tab="${name}"]`);
  if (btn) btn.classList.add('active');

  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  const pane = document.getElementById(`tab-${name}`);
  if (pane) pane.classList.add('active');

  document.getElementById('search-bar').style.display = (name === 'search') ? 'flex' : 'none';

  if (name === 'gallery') initGallery();
  if (name === 'search') initSearch();

  window.location.hash = name;
}

// ── Custom events from gallery/search cards ──────────────────────────────

window.addEventListener('iris:similar', (e) => {
  switchTab('search');
  doSimilarSearch(e.detail.index);
});

window.addEventListener('iris:detail', (e) => {
  const index = e.detail.index;
  const card = document.querySelector(`.media-card[data-index="${index}"]`);
  if (!card) return;
  const old = document.getElementById('detail-panel');
  if (old) old.remove();
  const panel = document.createElement('div');
  panel.id = 'detail-panel';
  panel.className = 'detail-panel';
  const cap = card.querySelector('.caption')?.textContent || '';
  panel.innerHTML = `
    <h4>${cap}</h4>
    <p style="font-size:12px;color:var(--text-secondary);">Indice: ${index}</p>
    <button class="btn" onclick="this.parentElement.remove()">Fechar</button>`;
  card.after(panel);
});

// ── Sidebar build ────────────────────────────────────────────────────────

async function buildSidebar() {
  try {
    const info = await fetchInfo();
    document.getElementById('status-badge').textContent = `${info.total_records} itens`;

    const colData = await listCollections();
    const colList = document.getElementById('collections-list');
    if (colData.collections && colData.collections.length) {
      colList.innerHTML = colData.collections.map(c =>
        `<label class="filter-checkbox"><input type="checkbox" value="${c.id}" class="collection-filter"> ${c.name} (${c.count || 0})</label>`
      ).join('');
    } else {
      colList.innerHTML = '<span style="font-size:11px;color:var(--text-muted);">Nenhuma colecao</span>';
    }

    const concData = await listConcepts();
    const concList = document.getElementById('concepts-list');
    if (concData.concepts && concData.concepts.length) {
      concList.innerHTML = concData.concepts.map(c =>
        `<label class="filter-checkbox"><input type="checkbox" value="${c.id}" class="concept-filter"> ${c.name} (${c.assoc_count || 0})</label>`
      ).join('');
    } else {
      concList.innerHTML = '<span style="font-size:11px;color:var(--text-muted);">Nenhum conceito</span>';
    }

    document.querySelectorAll('.collection-filter, .concept-filter, #filtro-media-type').forEach(el => {
      el.addEventListener('change', () => { window.location.reload(); });
    });

  } catch (err) {
    document.getElementById('status-badge').textContent = 'offline';
    console.error('Sidebar build failed:', err);
  }
}

// ── Floating selection panel ─────────────────────────────────────────────

window.addEventListener('iris:selection-changed', () => {
  const n = window.__irisSelection.size;
  const panel = document.getElementById('floating-panel');
  if (n === 0) { panel.style.display = 'none'; return; }
  panel.style.display = 'flex';
  document.getElementById('floating-count').textContent = `${n} selecionado(s)`;
  const indices = [...window.__irisSelection.keys()].slice(0, 8);
  document.getElementById('floating-thumbs').innerHTML = indices.map(i => {
    const card = document.querySelector(`.media-card[data-index="${i}"]`);
    const img = card ? card.querySelector('img') : null;
    return img ? `<img src="${img.src}" alt="">` : '';
  }).join('');
});

// ── Trash selected ───────────────────────────────────────────────────────

document.getElementById('btn-trash-selected').addEventListener('click', async () => {
  const ids = [...window.__irisSelection.keys()];
  if (!ids.length) return;
  if (!confirm(`Mover ${ids.length} item(ns) para lixeira?`)) return;
  try {
    const result = await trashRecords(ids);
    toast(`Movidos: ${result.moved}, Falhas: ${result.failed}`, result.failed ? 'error' : 'success');
    window.__irisSelection.clear();
    window.dispatchEvent(new CustomEvent('iris:selection-changed'));
    window.location.reload();
  } catch (err) {
    toast(`Erro: ${err.message}`, 'error');
  }
});

// ── Toast ────────────────────────────────────────────────────────────────

function toast(msg, level = 'info') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${level}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => { el.remove(); }, 3500);
}

// ── Init ─────────────────────────────────────────────────────────────────

(function init() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
  window.addEventListener('hashchange', () => {
    switchTab(window.location.hash.slice(1) || 'gallery');
  });
  buildSidebar();
  switchTab(window.location.hash.slice(1) || 'gallery');
})();

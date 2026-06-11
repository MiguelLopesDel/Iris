/* ── Iris Search module ──────────────────────────────────────────────────── */

import { searchText, searchImage, searchSimilar, searchRandom, debounce, escapeHtml } from './api.js';

// ── State ────────────────────────────────────────────────────────────────
let lastQuery = '';
let searchMode = 'text'; // 'text' | 'image' | 'similar' | 'random'

// ── Init ─────────────────────────────────────────────────────────────────

export function initSearch() {
  const input = document.getElementById('search-input2');
  const fileInput = document.getElementById('search-image-input2');
  const backBtn = document.getElementById('btn-similar-back');

  // Text search with debounce
  input.addEventListener('input', debounce(() => {
    const q = input.value.trim();
    if (q && q.length >= 1) {
      lastQuery = q;
      searchMode = 'text';
      doTextSearch(q);
    }
  }, 300));

  // Image upload
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) {
      searchMode = 'image';
      doImageSearch(fileInput.files[0]);
    }
  });

  // Back from similar
  backBtn.addEventListener('click', () => {
    backBtn.style.display = 'none';
    input.value = '';
    document.getElementById('search-results-grid').innerHTML = '';
    document.getElementById('search-results-info').textContent = '';
  });
}

// ── Search actions ───────────────────────────────────────────────────────

async function doTextSearch(q) {
  const grid = document.getElementById('search-results-grid');
  const info = document.getElementById('search-results-info');
  grid.innerHTML = '<p style="color:var(--text-muted);">Buscando...</p>';
  try {
    const data = await searchText(q);
    info.textContent = `${data.total} resultado(s) para "${q}"`;
    renderResults(data.results);
    document.getElementById('btn-similar-back').style.display = 'none';
  } catch (err) {
    grid.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

async function doImageSearch(file) {
  const grid = document.getElementById('search-results-grid');
  const info = document.getElementById('search-results-info');
  grid.innerHTML = '<p style="color:var(--text-muted);">Buscando por imagem...</p>';
  try {
    const data = await searchImage(file);
    info.textContent = `${data.total} resultado(s) para "${data.filename}"`;
    renderResults(data.results);
  } catch (err) {
    grid.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

export async function doSimilarSearch(idx) {
  const grid = document.getElementById('search-results-grid');
  const info = document.getElementById('search-results-info');
  grid.innerHTML = '<p style="color:var(--text-muted);">Buscando similares...</p>';
  try {
    const data = await searchSimilar(idx);
    info.textContent = `${data.total} resultado(s) similares`;
    renderResults(data.results);
    document.getElementById('btn-similar-back').style.display = 'inline-block';
  } catch (err) {
    grid.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

export async function doRandomSearch(n = 20) {
  const grid = document.getElementById('search-results-grid');
  const info = document.getElementById('search-results-info');
  grid.innerHTML = '<p style="color:var(--text-muted);">Carregando aleatorios...</p>';
  try {
    const data = await searchRandom(n);
    info.textContent = `${data.total} resultado(s) aleatorio(s)`;
    renderResults(data.results);
  } catch (err) {
    grid.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

// ── Render results ───────────────────────────────────────────────────────

function renderResults(results) {
  const grid = document.getElementById('search-results-grid');
  if (!results.length) {
    grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Nenhum resultado.</p>';
    return;
  }
  grid.innerHTML = results.map(r => renderResultCard(r)).join('');
}

function renderResultCard(r) {
  const score = (r.score || 0).toFixed(3);
  const thumb = r.thumbnail_url || '';
  const name = escapeHtml(r.arquivo).slice(0, 50);
  const sel = window.__irisSelection.has(r.index) ? 'checked' : '';

  if (!thumb) {
    return `<div class="media-card" data-index="${r.index}">
      <div class="placeholder-card"><span class="icon">🖼️</span><span>indisponivel</span></div>
      <div class="media-card-body">
        <span class="score-badge" style="position:static;">${score}</span>
        <div class="caption">${name}</div>
      </div>
    </div>`;
  }

  const imgTag = `<img src="${thumb}" loading="lazy" alt="${name}">`;
  const playBtn = r.media_type === 'video'
    ? `<button class="play-overlay" data-index="${r.index}" data-path="${escapeHtml(r.resolved_path || '')}">▶</button>`
    : '';

  return `<div class="media-card" data-index="${r.index}">
    <div class="media-card-img" id="card-img-${r.index}">
      ${imgTag}
      ${playBtn}
      <label class="score-badge">${score}</label>
      <input type="checkbox" class="media-checkbox" id="sel_${r.index}" data-index="${r.index}" ${sel}>
    </div>
    <div class="media-card-body">
      <div class="caption" title="${escapeHtml(r.arquivo)}">${name}</div>
      <div class="actions">
        <button class="btn" data-action="similar" data-index="${r.index}">Similares</button>
        <button class="btn" data-action="detail" data-index="${r.index}">Detalhes</button>
      </div>
    </div>
  </div>`;
}

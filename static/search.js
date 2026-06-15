/* ── Iris Search module ──────────────────────────────────────────────────── */

import { searchText, searchImage, searchSimilar, searchRandom, debounce, escapeHtml } from './api.js?v=24';

// ── State ────────────────────────────────────────────────────────────────
let lastQuery = '';
let searchMode = 'text'; // 'text' | 'image' | 'similar' | 'random'

// ── Search options from sidebar ──────────────────────────────────────────

function gatherSearchOptions() {
  const mode = document.getElementById('search-mode')?.value || 'hybrid';
  const top_k = parseInt(document.getElementById('search-topk')?.value) || 50;
  const threshold = parseFloat(document.getElementById('search-threshold')?.value) || 0.15;
  const translate = document.getElementById('search-translate')?.checked ?? true;

  let balance = 0.5, text_bonus = 2.0, lexical_weight = 0.25;
  if (mode === 'text') { balance = 0.0; text_bonus = 3.0; lexical_weight = 0.4; }
  else if (mode === 'visual') { balance = 0.65; text_bonus = 0.5; lexical_weight = 0.0; }
  else if (mode === 'custom') {
    balance = parseFloat(document.getElementById('search-balance')?.value) || 0.5;
    text_bonus = parseFloat(document.getElementById('search-textbonus')?.value) || 2.0;
    lexical_weight = parseFloat(document.getElementById('search-lexical')?.value) || 0.25;
  }

  return { top_k, threshold, balance, text_bonus, lexical_weight, translate };
}

// ── Init ─────────────────────────────────────────────────────────────────

export function initSearch() {
  const input = document.getElementById('search-input2');
  const fileInput = document.getElementById('search-image-input2');
  const backBtn = document.getElementById('btn-similar-back');
  const groupThreshold = document.getElementById('image-group-threshold');
  if (groupThreshold && groupThreshold.dataset.initialized !== 'true') {
    groupThreshold.dataset.initialized = 'true';
    groupThreshold.addEventListener('input', () => {
      document.getElementById('image-group-threshold-val').textContent =
        Number(groupThreshold.value).toFixed(2);
    });
  }
  if (input.dataset.initialized === 'true') return;
  input.dataset.initialized = 'true';

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
    const opts = gatherSearchOptions();
    const data = await searchText(q, opts);
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
    const opts = {
      ...gatherSearchOptions(),
      group_results: document.getElementById('image-group-enabled').checked,
      group_threshold: document.getElementById('image-group-threshold').value,
      show_singletons: document.getElementById('image-group-singletons').checked,
    };
    const data = await searchImage(file, opts);
    info.textContent = `${data.total} resultado(s) para "${data.filename}"`;
    if (Array.isArray(data.groups)) renderGroupedResults(data.groups);
    else renderResults(data.results);
  } catch (err) {
    grid.innerHTML = `<p style="color:var(--accent);">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

function renderGroupedResults(groups) {
  const grid = document.getElementById('search-results-grid');
  if (!groups.length) {
    grid.innerHTML = '<p style="color:var(--text-muted);padding:20px;">Nenhum grupo encontrado.</p>';
    return;
  }
  grid.innerHTML = groups.map((group, index) => `
    <section class="search-result-group">
      <div class="search-result-group-title">Grupo ${index + 1} · ${group.length} item(ns)</div>
      <div class="media-grid">${group.map(renderResultCard).join('')}</div>
    </section>
  `).join('');
}

export async function doSimilarSearch(idx) {
  const grid = document.getElementById('search-results-grid');
  const info = document.getElementById('search-results-info');
  grid.innerHTML = '<p style="color:var(--text-muted);">Buscando similares...</p>';
  try {
    const opts = gatherSearchOptions();
    const data = await searchSimilar(idx, opts);
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

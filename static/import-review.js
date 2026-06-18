/* ── Import review (deduplication quarantine) ────────────────────────────────
   Files the importer flagged as duplicates, grouped by detection category.
   Candidate (the file that tried to import) shown next to the existing match.

   Supports multi-selection with bulk ignore / trash / import (one request →
   one import job, so importing many at once never collides), plus a lightbox
   to inspect any candidate (and its match) at full size. */

import { escapeHtml, getImportReview, resolveImportReview } from './api.js?v=34';

let busy = false;
const selected = new Set(); // item ids currently checked (persist across categories)

export async function initImportReview() {
  const root = document.getElementById('import-review');
  if (!root) return;
  selected.clear();
  root.innerHTML = '<span class="filter-empty">Carregando revisão…</span>';
  try {
    const data = await getImportReview('');
    renderSummary(root, data);
  } catch (error) {
    root.innerHTML = `<span class="filter-empty">Erro: ${escapeHtml(error.message)}</span>`;
  }
}

function renderSummary(root, data) {
  if (!data.total) {
    root.innerHTML = '<span class="filter-empty">Nenhuma duplicata pendente. ✓</span>';
    return;
  }
  const header = `<p class="ir-total"><strong id="ir-total-n">${data.total}</strong> item(ns) em revisão. `
    + 'Clique numa imagem para vê-la inteira; use os botões direto (pode clicar em sequência) '
    + 'ou marque vários e decida em massa.</p>';
  const bar = `
    <div class="ir-selbar" id="ir-selbar" hidden>
      <span class="ir-selcount">0 selecionado(s)</span>
      <button class="btn btn-subtle" data-sel="import">Importar selecionadas</button>
      <button class="btn btn-subtle" data-sel="ignore">Ignorar selecionadas</button>
      <button class="btn btn-danger" data-sel="trash">Mover p/ lixeira</button>
      <button class="btn btn-ghost" data-sel="clear">Limpar seleção</button>
    </div>`;
  const blocks = data.categories.map(cat => categoryBlock(cat)).join('');
  root.innerHTML = header + bar + blocks;

  root.querySelectorAll('details.ir-cat').forEach(details => {
    details.addEventListener('toggle', () => {
      if (details.open && !details.dataset.loaded) loadCategory(details);
    });
    if (details.open) loadCategory(details);
  });
  root.querySelectorAll('[data-bulk]').forEach(button => {
    button.addEventListener('click', () => bulkResolve(button.dataset.detection, button.dataset.bulk));
  });
  root.querySelectorAll('[data-sel]').forEach(button => {
    button.addEventListener('click', () => selectionAction(button.dataset.sel));
  });
  updateSelBar();
}

function categoryBlock(cat) {
  const collapsed = cat.detection === 'exact_hash' || cat.detection === 'deleted_registry';
  return `
    <details class="ir-cat"${collapsed ? '' : ' open'} data-detection="${cat.detection}">
      <summary>
        <span class="ir-cat-label">${escapeHtml(cat.label)}</span>
        <span class="ir-cat-count">${cat.count}</span>
        <span class="ir-cat-bulk">
          <button class="btn btn-subtle" data-bulk="selectall" data-detection="${cat.detection}">Selecionar todas</button>
          <button class="btn btn-subtle" data-bulk="ignore" data-detection="${cat.detection}">Ignorar todas</button>
          <button class="btn btn-danger" data-bulk="trash" data-detection="${cat.detection}">Mover originais p/ lixeira</button>
        </span>
      </summary>
      <div class="ir-items" data-detection="${cat.detection}">
        <span class="filter-empty">Abra para carregar…</span>
      </div>
    </details>`;
}

async function loadCategory(details) {
  const detection = details.dataset.detection;
  const container = details.querySelector('.ir-items');
  container.innerHTML = '<span class="filter-empty">Carregando…</span>';
  try {
    const data = await getImportReview(detection, 200, 0);
    if (!data.items.length) {
      container.innerHTML = '<span class="filter-empty">Nada pendente nesta categoria.</span>';
    } else {
      container.innerHTML = data.items.map(itemCard).join('');
      const remaining = (data.categories.find(c => c.detection === detection) || {}).count || data.items.length;
      if (remaining > data.items.length) {
        container.innerHTML += `<p class="ir-more">Mostrando ${data.items.length} de ${remaining}. `
          + 'Resolva estes para ver os próximos.</p>';
      }
      wireItems(container, data.items);
    }
    details.dataset.loaded = '1';
  } catch (error) {
    container.innerHTML = `<span class="filter-empty">Erro: ${escapeHtml(error.message)}</span>`;
  }
}

function wireItems(container, items) {
  const byId = new Map(items.map(it => [String(it.id), it]));
  container.querySelectorAll('[data-act]').forEach(button => {
    button.addEventListener('click', () => itemResolve(button.dataset.id, button.dataset.act));
  });
  container.querySelectorAll('.ir-check').forEach(box => {
    box.checked = selected.has(Number(box.dataset.id));
    box.addEventListener('change', () => {
      const id = Number(box.dataset.id);
      if (box.checked) selected.add(id); else selected.delete(id);
      updateSelBar();
    });
  });
  container.querySelectorAll('[data-zoom]').forEach(fig => {
    fig.addEventListener('click', () => openLightbox(byId.get(fig.dataset.zoom)));
  });
}

function itemCard(item) {
  const candThumb = item.candidate_thumb_url || item.match_thumb_url;
  const candImg = candThumb
    ? `<img loading="lazy" src="${escapeHtml(candThumb)}" alt="">`
    : '<span class="ir-noimg">sem prévia</span>';
  const matchImg = item.match_thumb_url
    ? `<img loading="lazy" src="${escapeHtml(item.match_thumb_url)}" alt="">`
    : '<span class="ir-noimg">—</span>';
  const matchCaption = item.match_filename
    ? `no banco: ${escapeHtml(item.match_filename)}`
    : 'registro de deletado';
  return `
    <div class="ir-item" data-id="${item.id}">
      <label class="ir-select"><input type="checkbox" class="ir-check" data-id="${item.id}"><span>selecionar</span></label>
      <div class="ir-pair">
        <figure class="ir-zoom" data-zoom="${item.id}" title="Ver imagem inteira">${candImg}<figcaption>candidato: ${escapeHtml(item.candidate_filename)}</figcaption></figure>
        <span class="ir-vs">↔ ${item.score.toFixed(3)}</span>
        <figure class="ir-zoom" data-zoom="${item.id}" title="Ver imagem inteira">${matchImg}<figcaption>${matchCaption}</figcaption></figure>
      </div>
      <div class="ir-actions">
        <button class="btn btn-subtle" data-act="ignore" data-id="${item.id}">Ignorar</button>
        <button class="btn btn-subtle" data-act="import" data-id="${item.id}">Importar mesmo assim</button>
        <button class="btn btn-danger" data-act="trash" data-id="${item.id}">Mover p/ lixeira</button>
      </div>
    </div>`;
}

function updateSelBar() {
  const bar = document.getElementById('ir-selbar');
  if (!bar) return;
  const n = selected.size;
  bar.hidden = n === 0;
  const label = bar.querySelector('.ir-selcount');
  if (label) label.textContent = `${n} selecionado(s)`;
}

// ── Lightbox ───────────────────────────────────────────────────────────────

function openLightbox(item) {
  if (!item) return;
  const existing = document.getElementById('ir-lightbox');
  if (existing) existing.remove();
  const cand = item.candidate_full_url || item.candidate_thumb_url || '';
  const match = item.match_full_url || item.match_thumb_url || '';
  const matchSide = match
    ? `<figure><img src="${escapeHtml(match)}" alt=""><figcaption>no banco: ${escapeHtml(item.match_filename || '—')}</figcaption></figure>`
    : '';
  const overlay = document.createElement('div');
  overlay.id = 'ir-lightbox';
  overlay.className = 'ir-lightbox';
  overlay.innerHTML = `
    <div class="ir-lb-inner">
      <button class="ir-lb-close" title="Fechar (Esc)">✕</button>
      <div class="ir-lb-pair">
        <figure><img src="${escapeHtml(cand)}" alt=""><figcaption>candidato: ${escapeHtml(item.candidate_filename)}</figcaption></figure>
        ${matchSide}
      </div>
    </div>`;
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  overlay.querySelector('.ir-lb-close').addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
}

// ── Resolve actions ──────────────────────────────────────────────────────────

async function itemResolve(id, action) {
  // Optimistic + non-blocking: remove the card immediately and fire the request in
  // the background, so the user can click through items in a row without waiting.
  // "import" enqueues server-side (no per-click job), so rapid clicks never collide.
  const card = document.querySelector(`.ir-item[data-id="${id}"]`);
  if (!card) return;
  selected.delete(Number(id));
  applyRemoval(card);
  updateSelBar();
  try {
    await resolveImportReview({ ids: [Number(id)], action });
  } catch (error) {
    alert(`Erro: ${error.message}`);
    await initImportReview(); // resync on failure
  }
}

function applyRemoval(card) {
  const details = card.closest('details.ir-cat');
  card.remove();
  if (details) {
    const badge = details.querySelector('.ir-cat-count');
    if (badge) {
      const n = Math.max(0, (parseInt(badge.textContent, 10) || 1) - 1);
      badge.textContent = String(n);
      if (n === 0) details.remove();
    }
  }
  const totalEl = document.getElementById('ir-total-n');
  if (totalEl) {
    const t = Math.max(0, (parseInt(totalEl.textContent, 10) || 1) - 1);
    totalEl.textContent = String(t);
    if (t === 0) {
      const root = document.getElementById('import-review');
      if (root) root.innerHTML = '<span class="filter-empty">Nenhuma duplicata pendente. ✓</span>';
    }
  }
}

async function selectionAction(action) {
  if (action === 'clear') {
    selected.clear();
    document.querySelectorAll('.ir-check').forEach(b => { b.checked = false; });
    updateSelBar();
    return;
  }
  if (busy || !selected.size) return;
  const ids = [...selected];
  if (action === 'trash' && !confirm(`Mover ${ids.length} original(is) para a lixeira?`)) return;
  busy = true;
  try {
    await resolveImportReview({ ids, action });
    await initImportReview();
  } catch (error) {
    alert(`Erro: ${error.message}`);
  } finally {
    busy = false;
  }
}

async function bulkResolve(detection, action) {
  if (action === 'selectall') {
    selectAllInCategory(detection);
    return;
  }
  if (busy) return;
  const verb = action === 'trash' ? 'mover os originais para a lixeira' : 'ignorar';
  if (!confirm(`Confirma ${verb} de TODOS os itens em "${detection}"?`)) return;
  busy = true;
  try {
    await resolveImportReview({ detection, action });
    await initImportReview();
  } catch (error) {
    alert(`Erro: ${error.message}`);
  } finally {
    busy = false;
  }
}

function selectAllInCategory(detection) {
  const container = document.querySelector(`.ir-items[data-detection="${detection}"]`);
  if (!container) return;
  container.querySelectorAll('.ir-check').forEach(box => {
    box.checked = true;
    selected.add(Number(box.dataset.id));
  });
  updateSelBar();
}

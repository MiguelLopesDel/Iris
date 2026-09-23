/* ── Iris trash of the private library ──────────────────────────────────────
   What was moved to the trash stays here, restorable, until its date; then
   the server deletes the original. Restoring brings the photo back with its
   albums, description and faces. */

import { escapeHtml, listTrash, restoreTrash } from './api.js?v=45';
import { toast } from './ui.js?v=4';

let initialized = false;
let cursor = null;
const selected = new Set();

const $ = (id) => document.getElementById(id);

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleDateString('pt-BR', { dateStyle: 'medium' }) : '';
}

function card(item) {
  const name = escapeHtml(item.name);
  const media = item.thumbnail_url
    ? `<img src="${escapeHtml(item.thumbnail_url)}" loading="lazy" alt="${name}">`
    : '<div class="placeholder-card"><span class="icon">🖼️</span><span>sem arquivo</span></div>';
  return `<div class="media-card" data-trash-item="${item.id}">
    <div class="media-card-img">${media}
      <input type="checkbox" class="media-checkbox" data-trash-select="${item.id}"
        aria-label="Selecionar ${name}"${selected.has(item.id) ? ' checked' : ''}>
    </div>
    <div class="media-card-body">
      <div class="caption" title="${name}">${name}</div>
      <div class="space-item-author">apagada de vez em ${formatDate(item.purge_after)}</div>
      <div class="actions">
        <button class="btn" type="button" data-trash-restore="${item.id}">Restaurar</button>
      </div>
    </div>
  </div>`;
}

async function load(reset) {
  const grid = $('trash-items');
  if (reset) {
    cursor = null;
    selected.clear();
    grid.innerHTML = '<p class="filter-empty">Carregando...</p>';
  }
  try {
    const page = await listTrash({ before: cursor });
    $('trash-hint').textContent = `Itens apagados ficam aqui por ${page.trash_days} dias e voltam`
      + ' com álbuns, descrição e rostos. Depois dessa data o Iris apaga o original de vez.';
    if (reset) grid.innerHTML = '';
    grid.insertAdjacentHTML('beforeend', page.items.map(card).join(''));
    if (reset && !page.items.length) {
      grid.innerHTML = `<div class="empty-state"><span class="empty-state-icon">↺</span>
        <p>A lixeira está vazia.</p></div>`;
    }
    cursor = page.next_before;
    $('trash-more').hidden = !cursor;
  } catch (error) {
    grid.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
  syncSelection();
}

function syncSelection() {
  const button = $('trash-restore-selected');
  button.disabled = selected.size === 0;
  button.textContent = selected.size ? `Restaurar ${selected.size} selecionado(s)` : 'Restaurar selecionados';
}

async function restore(ids) {
  try {
    const result = await restoreTrash(ids);
    for (const id of result.restored) {
      document.querySelector(`[data-trash-item="${id}"]`)?.remove();
      selected.delete(id);
    }
    if (result.restored.length) {
      toast(`${result.restored.length} item(ns) de volta à biblioteca.`, 'success');
      // The gallery and home page cached the old catalogue.
      window.dispatchEvent(new CustomEvent('iris:library-changed'));
    }
    if (result.conflicts.length) {
      toast(`${result.conflicts.length} não voltaram: já existe outro arquivo no lugar original.`, 'error');
    }
    if (!$('trash-items').querySelector('[data-trash-item]')) load(true);
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
  syncSelection();
}

export function initTrash() {
  if (!initialized) {
    initialized = true;
    $('trash-more').addEventListener('click', () => load(false));
    $('trash-restore-selected').addEventListener('click', () => restore([...selected]));
    $('trash-items').addEventListener('click', (event) => {
      const button = event.target.closest('[data-trash-restore]');
      if (button) restore([Number(button.dataset.trashRestore)]);
    });
    $('trash-items').addEventListener('change', (event) => {
      const box = event.target.closest('[data-trash-select]');
      if (!box) return;
      const id = Number(box.dataset.trashSelect);
      if (box.checked) selected.add(id); else selected.delete(id);
      syncSelection();
    });
  }
  load(true);
}

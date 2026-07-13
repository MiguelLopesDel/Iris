/* ── Iris UI primitives ─────────────────────────────────────────────────────
   Shared toast + promise-based modal dialogs that replace the native
   prompt()/confirm()/alert(). Every module should use these instead of
   ad-hoc dialogs so the whole app shares one visual language.

   Z-index scale (keep in sync with style.css):
     60 floating-panel · 110 web-enrichment · 190 detail-modal ·
     200 image-lightbox · 300 app-modal · 400 toasts */

import { escapeHtml } from './api.js?v=39';

export function toast(msg, level) {
  level = level || 'info';
  const container = document.getElementById('toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = 'toast ' + level;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/**
 * Open a modal dialog. Returns { el, body, close } where `body` is the content
 * container. `body` option accepts an HTML string or a Node; `actions` is a list
 * of { label, primary, danger, onClick } — onClick(close) decides when to close.
 * Esc, the × button and a backdrop click all close (calling onCancel if given).
 */
export function openModal({ kicker = '', title = '', body = '', actions = [], onCancel = null } = {}) {
  const overlay = document.createElement('div');
  overlay.className = 'app-modal';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');

  const card = document.createElement('div');
  card.className = 'app-modal-card';

  const header = document.createElement('header');
  const heading = document.createElement('div');
  if (kicker) {
    const eyebrow = document.createElement('span');
    eyebrow.className = 'eyebrow';
    eyebrow.textContent = kicker;
    heading.appendChild(eyebrow);
  }
  const h3 = document.createElement('h3');
  h3.textContent = title;
  heading.appendChild(h3);
  const closeBtn = document.createElement('button');
  closeBtn.className = 'icon-btn';
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Fechar');
  closeBtn.textContent = '×';
  header.appendChild(heading);
  header.appendChild(closeBtn);

  const bodyEl = document.createElement('div');
  bodyEl.className = 'app-modal-body';
  if (typeof body === 'string') bodyEl.innerHTML = body;
  else if (body) bodyEl.appendChild(body);

  card.appendChild(header);
  card.appendChild(bodyEl);

  let footer = null;
  if (actions.length) {
    footer = document.createElement('footer');
    footer.className = 'app-modal-actions';
    for (const action of actions) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn ' + (action.primary ? 'btn-primary' : 'btn-subtle') + (action.danger ? ' btn-danger' : '');
      btn.textContent = action.label;
      btn.addEventListener('click', () => action.onClick ? action.onClick(close) : close());
      footer.appendChild(btn);
    }
    card.appendChild(footer);
  }

  overlay.appendChild(card);
  document.body.appendChild(overlay);

  function close() {
    document.removeEventListener('keydown', onKey, true);
    overlay.remove();
  }
  function cancel() {
    close();
    if (onCancel) onCancel();
  }
  function onKey(e) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      cancel();
    }
  }
  document.addEventListener('keydown', onKey, true);
  closeBtn.addEventListener('click', cancel);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) cancel(); });

  return { el: overlay, body: bodyEl, close };
}

/** confirm() replacement. Resolves true/false. */
export function confirmModal(message, { kicker = 'Confirmar', title = 'Tem certeza?', confirmLabel = 'Confirmar', cancelLabel = 'Cancelar', danger = false } = {}) {
  return new Promise((resolve) => {
    openModal({
      kicker,
      title,
      body: `<p>${escapeHtml(message)}</p>`,
      onCancel: () => resolve(false),
      actions: [
        { label: cancelLabel, onClick: (close) => { close(); resolve(false); } },
        { label: confirmLabel, primary: true, danger, onClick: (close) => { close(); resolve(true); } },
      ],
    });
  });
}

/** prompt() replacement. Resolves the typed string, or null when cancelled. */
export function promptModal({ kicker = '', title = '', label = '', value = '', placeholder = '', confirmLabel = 'Salvar' } = {}) {
  return new Promise((resolve) => {
    const form = document.createElement('form');
    if (label) {
      const labelEl = document.createElement('label');
      labelEl.className = 'app-modal-label';
      labelEl.textContent = label;
      form.appendChild(labelEl);
    }
    const input = document.createElement('input');
    input.type = 'text';
    input.value = value;
    input.placeholder = placeholder;
    input.autocomplete = 'off';
    form.appendChild(input);

    const modal = openModal({
      kicker,
      title,
      body: form,
      onCancel: () => resolve(null),
      actions: [
        { label: 'Cancelar', onClick: (close) => { close(); resolve(null); } },
        { label: confirmLabel, primary: true, onClick: (close) => { close(); resolve(input.value); } },
      ],
    });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      modal.close();
      resolve(input.value);
    });
    requestAnimationFrame(() => { input.focus(); input.select(); });
  });
}

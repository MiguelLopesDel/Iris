/* ── Iris Duplicates module ───────────────────────────────────────────────── */

import { fetchDuplicates } from './api.js';

export function initDuplicates() {
  const slider = document.getElementById('dup-threshold');
  slider.oninput = () => {
    document.getElementById('dup-threshold-val').textContent = slider.value;
  };
  document.getElementById('btn-find-duplicates').onclick = loadDuplicates;
}

async function loadDuplicates() {
  const container = document.getElementById('duplicates-results');
  const threshold = parseFloat(document.getElementById('dup-threshold').value);
  container.innerHTML = '<p style="color:var(--text-muted);">Buscando duplicatas...</p>';
  try {
    const data = await fetchDuplicates(threshold);
    if (!data.total_groups) {
      container.innerHTML = '<p style="color:var(--text-muted);">Nenhuma duplicata encontrada.</p>';
      return;
    }
    container.innerHTML = `<p>${data.total_groups} grupo(s) encontrados (threshold: ${threshold})</p>` +
      data.groups.map(g => `
        <div class="detail-panel" style="margin-bottom:8px;">
          <strong>Grupo #${g.group_id}</strong> (${g.kind}, score: ${g.score.toFixed(4)})
          <ul style="font-size:11px;color:var(--text-secondary);margin-top:4px;">
            ${g.items.map(it => `<li>${it.arquivo} (${it.score_to_anchor.toFixed(4)})</li>`).join('')}
          </ul>
        </div>
      `).join('');
  } catch (err) {
    container.innerHTML = `<p style="color:var(--accent);">Erro: ${err.message}</p>`;
  }
}

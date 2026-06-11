/* ── Iris Concepts module ─────────────────────────────────────────────────── */

import { listConcepts, createConcept, deleteConcept } from './api.js';

export function initConcepts() {
  loadConcepts();
  document.getElementById('btn-new-concept').onclick = () => {
    const name = prompt('Nome do conceito:');
    if (!name) return;
    const cat = prompt('Categoria:', 'outro');
    createConcept({ name, category: cat || 'outro' }).then(loadConcepts);
  };
}

async function loadConcepts() {
  const container = document.getElementById('concepts-tab-list');
  try {
    const data = await listConcepts();
    if (!data.concepts.length) {
      container.innerHTML = '<p style="color:var(--text-muted);">Nenhum conceito.</p>';
      return;
    }
    container.innerHTML = data.concepts.map(c => `
      <div class="detail-panel" style="margin-bottom:8px;">
        <strong>${c.name}</strong> <span style="color:var(--text-muted);">(${c.category})</span>
        <span style="font-size:11px;color:var(--text-secondary);"> — ${c.assoc_count || 0} associacoes, ${c.ref_count || 0} refs</span>
        <button class="btn btn-danger" style="float:right;" onclick="if(confirm('Deletar?'))__irisDelConcept(${c.id})">Deletar</button>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p style="color:var(--accent);">Erro: ${err.message}</p>`;
  }
}

window.__irisDelConcept = async (id) => { await deleteConcept(id); loadConcepts(); };

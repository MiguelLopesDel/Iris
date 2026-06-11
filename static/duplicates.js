/* ── Iris Duplicates module ───────────────────────────────────────────────── */

import { fetchDuplicates } from './api.js';

var viewMode = 'groups'; // 'groups' | 'flat'

export function initDuplicates() {
  var slider = document.getElementById('dup-threshold');
  if (slider) {
    slider.oninput = function() {
      document.getElementById('dup-threshold-val').textContent = this.value;
    };
  }
  document.getElementById('btn-find-duplicates').onclick = loadDuplicates;

  // Add view mode toggle
  var controls = document.getElementById('duplicates-tab-content');
  var modeHtml = '<label style="margin-left:10px;">Visualizacao: <select id="dup-view-mode">'
    + '<option value="groups">Por grupos</option>'
    + '<option value="flat">Lista unica</option>'
    + '</select></label>'
    + '<label style="margin-left:10px;">Tam. minimo: <input type="number" id="dup-min-group" value="2" min="2" max="50" style="width:60px;"></label>';
  controls.insertAdjacentHTML('beforeend', modeHtml);

  document.getElementById('dup-view-mode').onchange = function() {
    viewMode = this.value;
    loadDuplicates();
  };
}

async function loadDuplicates() {
  var container = document.getElementById('duplicates-results');
  var threshold = parseFloat(document.getElementById('dup-threshold').value);
  var minGroup = parseInt(document.getElementById('dup-min-group')?.value) || 2;
  container.innerHTML = '<p style="color:var(--text-muted);">Buscando duplicatas...</p>';
  try {
    var data = await fetchDuplicates(threshold, 12);
    var groups = data.groups.filter(function(g) { return g.items.length >= minGroup; });
    if (!groups.length) {
      container.innerHTML = '<p style="color:var(--text-muted);padding:16px;">Nenhuma duplicata encontrada (threshold: ' + threshold + ').</p>';
      return;
    }
    var totalItems = groups.reduce(function(s, g) { return s + g.items.length; }, 0);
    container.innerHTML = '<p style="margin-bottom:10px;">' + groups.length + ' grupo(s), ' + totalItems + ' imagem(ns) envolvidas (threshold: ' + threshold + ')</p>'
      + (viewMode === 'flat' ? renderFlat(groups) : renderGroups(groups));
  } catch (err) {
    container.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>';
  }
}

function renderGroups(groups) {
  return groups.map(function(g) {
    return '<details class="detail-panel" style="margin-bottom:8px;" ' + (g.group_id <= 5 ? 'open' : '') + '>'
      + '<summary>Grupo #' + g.group_id + ' — ' + g.items.length + ' imagens — score ' + g.score.toFixed(4) + '</summary>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:8px;">'
      + g.items.map(renderItem).join('')
      + '</div></details>';
  }).join('');
}

function renderFlat(groups) {
  var html = '';
  groups.forEach(function(g) {
    html += '<div style="margin:8px 0;padding:4px 0;border-bottom:1px solid var(--border);"><strong>Grupo #' + g.group_id + '</strong> | ' + g.items.length + ' imagens | score ' + g.score.toFixed(4) + '</div>';
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;">';
    html += g.items.map(renderItem).join('');
    html += '</div>';
  });
  return html;
}

function renderItem(item) {
  var thumbUrl = '/thumbs/__unknown__'; // Will be determined
  return '<div style="font-size:10px;text-align:center;">'
    + '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;display:flex;align-items:center;justify-content:center;overflow:hidden;">'
    + '<span style="font-size:24px;">🖼️</span>'
    + '</div>'
    + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:2px;" title="' + item.arquivo + '">' + item.arquivo.slice(0, 35) + '</div>'
    + '<div style="color:var(--text-muted);">' + item.score_to_anchor.toFixed(4) + '</div>'
    + '<div style="margin-top:2px;">'
    + '<button class="btn" style="font-size:9px;padding:1px 4px;" onclick="window.dispatchEvent(new CustomEvent(\'iris:similar\',{detail:{index:' + item.index + '}}))">Similares</button>'
    + '<button class="btn" style="font-size:9px;padding:1px 4px;" onclick="navigator.clipboard.writeText(\'' + (item.resolved_path || '') + '\')">📋 Path</button>'
    + '</div></div>';
}

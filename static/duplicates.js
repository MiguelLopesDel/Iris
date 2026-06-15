/* ── Iris Duplicates module ───────────────────────────────────────────────── */

import { escapeHtml, fetchDuplicates } from './api.js?v=24';

var viewMode = 'groups'; // 'groups' | 'flat'

export function initDuplicates() {
  var slider = document.getElementById('dup-threshold');
  if (slider) {
    slider.oninput = function() {
      document.getElementById('dup-threshold-val').textContent = this.value;
    };
  }
  document.getElementById('btn-find-duplicates').onclick = loadDuplicates;
  document.getElementById('dup-view-mode').onchange = function() {
    viewMode = this.value;
    loadDuplicates();
  };

  // Delegate: copy-path buttons use data attributes (safe from onclick escaping bugs)
  document.getElementById('duplicates-results').onclick = function(e) {
    var btn = e.target.closest('button');
    if (!btn) return;
    var path = btn.dataset.path;
    if (path) {
      navigator.clipboard.writeText(path).catch(function() {});
      btn.textContent = '✓ Copiado!';
      setTimeout(function() { btn.textContent = '📎 Path'; }, 1500);
    }
  };
}

async function loadDuplicates() {
  var container = document.getElementById('duplicates-results');
  var threshold = parseFloat(document.getElementById('dup-threshold').value);
  var minGroup = parseInt(document.getElementById('dup-min-group')?.value) || 2;
  var neighbors = parseInt(document.getElementById('dup-neighbors')?.value) || 12;
  var sortMode = document.getElementById('dup-sort')?.value || 'similarity';
  container.innerHTML = '<p style="color:var(--text-muted);">Buscando duplicatas...</p>';
  try {
    var data = await fetchDuplicates(threshold, neighbors, minGroup);
    var groups = sortDuplicateGroups(data.groups, sortMode);
    if (!groups.length) {
      container.innerHTML = '<p style="color:var(--text-muted);padding:16px;">Nenhuma duplicata encontrada (threshold: ' + threshold + ').</p>';
      return;
    }
    await hydrateMissingThumbnails(groups);
    var totalItems = groups.reduce(function(s, g) { return s + g.items.length; }, 0);
    container.innerHTML = '<p style="margin-bottom:10px;">' + groups.length + ' grupo(s), ' + totalItems + ' imagem(ns) envolvidas (threshold: ' + threshold + ')</p>'
      + (viewMode === 'flat' ? renderFlat(groups) : renderGroups(groups));
    installThumbnailFallbacks(container);
  } catch (err) {
    container.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>';
  }
}

function sortDuplicateGroups(groups, mode) {
  if (mode === 'similarity') return groups;
  var direction = mode === 'newest' ? -1 : 1;
  var sorted = groups.map(function(group) {
    return {
      ...group,
      items: [...group.items].sort(function(left, right) {
        return direction * ((left.file_mtime || 0) - (right.file_mtime || 0));
      }),
    };
  });
  return sorted.sort(function(left, right) {
    var leftTimes = left.items.map(function(item) { return item.file_mtime || 0; });
    var rightTimes = right.items.map(function(item) { return item.file_mtime || 0; });
    var leftTime = mode === 'newest' ? Math.max(...leftTimes) : Math.min(...leftTimes);
    var rightTime = mode === 'newest' ? Math.max(...rightTimes) : Math.min(...rightTimes);
    return direction * (leftTime - rightTime);
  });
}

async function hydrateMissingThumbnails(groups) {
  var queue = [];
  groups.forEach(function(group) {
    group.items.forEach(function(item) {
      if (!item.thumbnail_url) queue.push(item);
    });
  });
  if (!queue.length) return;

  var workerCount = Math.min(8, queue.length);
  await Promise.all(Array.from({ length: workerCount }, async function() {
    while (queue.length) {
      var item = queue.shift();
      try {
        var response = await fetch('/api/records/' + item.index);
        if (!response.ok) throw new Error('API ' + response.status);
        var record = await response.json();
        item.thumbnail_url = record.thumbnail_url || '';
        item.resolved_path = item.resolved_path || record.resolved_path || '';
      } catch (err) {
        console.warn('Falha ao carregar miniatura da duplicata', item.index, err);
      }
    }
  }));
}

function installThumbnailFallbacks(container) {
  container.querySelectorAll('[data-duplicate-thumb]').forEach(function(image) {
    function showPlaceholder() {
      var fallback = document.createElement('div');
      fallback.className = 'duplicate-thumb-fallback';
      fallback.textContent = 'Imagem indisponivel';
      image.replaceWith(fallback);
    }
    function handleError() {
      var fallbackSrc = image.dataset.fallbackSrc;
      if (fallbackSrc) {
        image.removeAttribute('data-fallback-src');
        image.addEventListener('error', showPlaceholder, { once: true });
        image.src = fallbackSrc;
        return;
      }
      showPlaceholder();
    }
    if (image.complete && image.naturalWidth === 0) {
      handleError();
    } else {
      image.addEventListener('error', handleError, { once: true });
    }
  });
}

function mediaUrlFromPath(path) {
  if (!path) return '';
  var normalized = String(path).replace(/^\/+/, '');
  return '/media/' + normalized.split('/').map(encodeURIComponent).join('/');
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
  var thumbHtml;
  var originalUrl = mediaUrlFromPath(item.resolved_path);
  var imageUrl = item.thumbnail_url || originalUrl;
  if (imageUrl) {
    var fallbackAttr = item.thumbnail_url && originalUrl && item.thumbnail_url !== originalUrl
      ? ' data-fallback-src="' + escapeHtml(originalUrl) + '"'
      : '';
    thumbHtml = '<img src="' + escapeHtml(imageUrl) + '" loading="lazy" data-duplicate-thumb'
      + fallbackAttr + ' style="width:100%;height:100%;object-fit:cover;">';
  } else {
    thumbHtml = '<div class="duplicate-thumb-fallback">Imagem indisponivel</div>';
  }
  // Build path-copy button via DOM to avoid onclick escaping hazards
  var pathId = 'dup-path-' + item.index;
  var safeScore = (item.score_to_anchor != null) ? item.score_to_anchor.toFixed(4) : '?';
  var safeArquivo = item.arquivo || '(sem nome)';
  var dateText = item.file_mtime
    ? new Date(item.file_mtime * 1000).toLocaleString()
    : 'data desconhecida';
  return '<div class="media-card" style="font-size:10px;text-align:center;">'
    + '<div class="media-card-img" style="overflow:hidden;">'
    + thumbHtml
    + '</div>'
    + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:2px;" title="' + escapeHtml(safeArquivo) + '">' + escapeHtml(safeArquivo.slice(0, 35)) + '</div>'
    + '<div style="color:var(--text-muted);">Score: ' + safeScore + '</div>'
    + '<div style="color:var(--text-muted);">' + escapeHtml(dateText) + '</div>'
    + '<div style="margin-top:2px;display:flex;gap:4px;justify-content:center;flex-wrap:wrap;">'
    + '<button class="btn" style="font-size:9px;padding:1px 4px;" onclick="window.dispatchEvent(new CustomEvent(\'iris:similar\',{detail:{index:' + item.index + '}}))">🔍 Similares</button>'
    + '<button class="btn" style="font-size:9px;padding:1px 4px;" onclick="window.dispatchEvent(new CustomEvent(\'iris:detail\',{detail:{index:' + item.index + '}}))">📋 Detalhes</button>'
    + '<button class="btn" style="font-size:9px;padding:1px 4px;" id="' + pathId + '" data-path="' + escapeHtml(item.resolved_path || '') + '">📎 Path</button>'
    + '</div></div>';
}

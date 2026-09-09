/* ── Iris Detail modal ──────────────────────────────────────────────────────
   Google-Photos-style viewer: media stage on the left, info aside on the
   right. Navigates the records currently rendered in the gallery (←/→),
   plays video natively, shows curated + full metadata on demand, and lets
   the user assign detected faces to persons. Replaces the old inline
   detail panel and, in the gallery, the image lightbox. */

import {
  assignFacePerson,
  escapeHtml,
  faceThumbUrl,
  getRecordFaces,
  getRecordMetadata,
  listPersons,
  mediaUrl,
} from './api.js?v=40';
import { toast } from './ui.js?v=1';
import { pickPersonModal } from './persons.js?v=5';

const modal = document.getElementById('detail-modal');
const stageMedia = document.getElementById('detail-media');
const asideEl = document.getElementById('detail-aside');
const counterEl = document.getElementById('detail-counter');
const prevBtn = document.getElementById('detail-prev');
const nextBtn = document.getElementById('detail-next');
const closeBtn = document.getElementById('detail-close');

const state = {
  records: [],   // entries: gallery records or `{ index }` stubs (merged after fetch)
  pos: -1,
  open: false,
  token: 0,      // guards async renders against fast navigation
};

// ── Public API ─────────────────────────────────────────────────────────────

export function openDetail(records, pos) {
  if (!modal || !records || !records.length) return;
  state.records = records;
  state.pos = Math.max(0, Math.min(pos, records.length - 1));
  state.open = true;
  modal.hidden = false;
  modal.classList.remove('immersive');
  document.body.classList.add('detail-open');
  render();
}

export function openDetailByIndex(index) {
  openDetail([{ index }], 0);
}

function closeDetail() {
  if (!state.open) return;
  state.open = false;
  state.token++;
  teardownVideo();
  modal.hidden = true;
  modal.classList.remove('immersive');
  document.body.classList.remove('detail-open');
}

function nav(delta) {
  const next = state.pos + delta;
  if (next < 0 || next >= state.records.length) return;
  state.pos = next;
  render();
}

// ── Stage ──────────────────────────────────────────────────────────────────

function teardownVideo() {
  const video = stageMedia.querySelector('video');
  if (video) {
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  stageMedia.innerHTML = '';
}

function hasFile(r) {
  return r && r.resolved_path && r.resolved_path !== 'None';
}

function renderStage(r) {
  teardownVideo();
  if (r.media_type === 'video' && hasFile(r)) {
    const video = document.createElement('video');
    video.controls = true;
    video.autoplay = true;
    video.muted = true;   // autoplay is only allowed muted; user unmutes in-player
    video.playsInline = true;
    video.src = mediaUrl(r.resolved_path);
    stageMedia.appendChild(video);
    return;
  }
  const img = document.createElement('img');
  img.alt = r.arquivo || '';
  const full = hasFile(r) && r.media_type !== 'video' ? mediaUrl(r.resolved_path) : '';
  img.src = full || r.thumbnail_url || '';
  if (full && r.thumbnail_url) {
    // Show the thumbnail instantly while the original streams in.
    img.style.backgroundImage = `url("${r.thumbnail_url}")`;
    img.style.backgroundSize = 'contain';
    img.style.backgroundRepeat = 'no-repeat';
    img.style.backgroundPosition = 'center';
  }
  img.addEventListener('error', () => {
    if (full && img.src !== r.thumbnail_url && r.thumbnail_url) img.src = r.thumbnail_url;
  }, { once: true });
  stageMedia.appendChild(img);
}

function preloadNeighbors() {
  for (const delta of [1, -1]) {
    const r = state.records[state.pos + delta];
    if (r && hasFile(r) && r.media_type === 'image') {
      const img = new Image();
      img.src = mediaUrl(r.resolved_path);
    }
  }
}

// ── Render orchestration ───────────────────────────────────────────────────

async function render() {
  const token = ++state.token;
  const stub = state.records[state.pos];
  const total = state.records.length;

  counterEl.textContent = total > 1 ? `${state.pos + 1} / ${total}` : '';
  prevBtn.hidden = total <= 1;
  nextBtn.hidden = total <= 1;
  prevBtn.disabled = state.pos <= 0;
  nextBtn.disabled = state.pos >= total - 1;

  if (stub.media_type) renderStage(stub);
  else teardownVideo();
  asideEl.innerHTML = '<div class="detail-loading">Carregando…</div>';

  let r;
  try {
    const res = await fetch('/api/records/' + stub.index);
    if (!res.ok) throw new Error('Registro não encontrado');
    r = await res.json();
  } catch (err) {
    if (token !== state.token) return;
    asideEl.innerHTML = `<div class="detail-loading">Erro: ${escapeHtml(err.message)}</div>`;
    return;
  }
  if (token !== state.token) return;

  // Merge so stage/nav can reuse the full record from now on.
  state.records[state.pos] = Object.assign({}, stub, r);
  if (!stub.media_type) renderStage(r);
  renderAside(r, token);
  preloadNeighbors();
}

// ── Aside ──────────────────────────────────────────────────────────────────

function section(label, contentHtml, { id = '', open = true } = {}) {
  if (!contentHtml) return '';
  const idAttr = id ? ` id="${id}"` : '';
  if (open) {
    return `<section class="detail-section"${idAttr}><h4>${label}</h4>${contentHtml}</section>`;
  }
  return `<details class="detail-section"${idAttr}><summary>${label}</summary>${contentHtml}</details>`;
}

function renderAside(r, token) {
  const sizeKb = r.file_size != null ? `${Math.round(r.file_size / 1024)} KB` : '';
  const typeLabel = r.media_type === 'video' ? 'Vídeo' : 'Imagem';
  const metaBits = [typeLabel, sizeKb, r.db_id ? `ID ${r.db_id}` : ''].filter(Boolean).join(' · ');

  const classif = [
    r.style && `<span>Estilo <strong>${escapeHtml(r.style)}</strong></span>`,
    r.source_work && `<span>Obra <strong>${escapeHtml(r.source_work)}</strong></span>`,
    r.context && `<span>Contexto <strong>${escapeHtml(r.context)}</strong></span>`,
    r.humor && `<span>Humor <strong>${escapeHtml(r.humor)}</strong></span>`,
  ].filter(Boolean).join('');

  asideEl.innerHTML = `
    <header class="detail-head">
      <span class="eyebrow">Detalhes</span>
      <h3 class="detail-title" title="${escapeHtml(r.arquivo || '')}">${escapeHtml(r.arquivo || '(sem nome)')}</h3>
      <p class="detail-meta">${escapeHtml(metaBits)}</p>
    </header>
    <div class="detail-actions">
      <button class="btn btn-subtle" data-detail-action="similar">Buscar similares</button>
      ${hasFile(r) ? `<button class="btn btn-subtle" data-open-folder="${escapeHtml(r.resolved_path)}">Abrir pasta</button>
      <a class="btn btn-subtle" href="${escapeHtml(mediaUrl(r.resolved_path))}" target="_blank" rel="noopener">Abrir arquivo</a>` : ''}
    </div>
    ${section('Pessoas', '<div class="detail-faces" id="detail-faces">…</div>')}
    ${section('Texto extraído', r.texto_extraido ? `<pre class="detail-pre">${escapeHtml(r.texto_extraido)}</pre>` : '', { open: false })}
    ${section('Descrição da IA', r.descricao_ia ? `<pre class="detail-pre">${escapeHtml(r.descricao_ia)}</pre>` : '', { open: false })}
    ${r.tags ? section('Tags', `<p class="detail-text">${escapeHtml(r.tags)}</p>`) : ''}
    ${classif ? section('Classificação', `<div class="detail-classif">${classif}</div>`) : ''}
    ${section('Álbuns', '<div class="detail-chips" id="detail-cols">Carregando…</div>')}
    ${section('Ensinados ao Iris', '<div class="detail-chips" id="detail-concs">Carregando…</div>')}
    <section class="detail-section" id="detail-metadata-section">
      <h4>Metadados</h4>
      <button class="btn btn-subtle" data-detail-action="metadata">Exibir metadados</button>
      <div id="detail-metadata"></div>
    </section>
  `;

  wireAsideActions(r);
  loadToggles(r, token);
  loadFaces(r, token);
}

function wireAsideActions(r) {
  const similar = asideEl.querySelector('[data-detail-action="similar"]');
  if (similar) {
    similar.addEventListener('click', () => {
      closeDetail();
      window.dispatchEvent(new CustomEvent('iris:similar', { detail: { index: r.index } }));
    });
  }
  const metaBtn = asideEl.querySelector('[data-detail-action="metadata"]');
  if (metaBtn) metaBtn.addEventListener('click', () => loadMetadata(r, metaBtn));
}

// ── Collections / concepts toggles ─────────────────────────────────────────

async function loadToggles(r, token) {
  try {
    const [colData, concData] = await Promise.all([
      fetch('/api/collections').then((x) => x.json()),
      fetch('/api/concepts').then((x) => x.json()),
    ]);
    if (token !== state.token) return;

    const inCol = new Set((r.collections || []).map((c) => c.id));
    const inConc = new Set((r.concepts || []).filter((c) => c.confirmed).map((c) => c.id));

    renderToggleChips(
      document.getElementById('detail-cols'), colData.collections || [], inCol,
      async (id, add) => {
        const url = add ? `/api/collections/${id}/members` : `/api/collections/${id}/members/remove`;
        await fetch(url, { method: 'POST', body: new URLSearchParams({ db_ids: String(r.db_id) }) });
        toast(add ? 'Adicionado ao álbum' : 'Removido do álbum', 'success');
      },
    );
    renderToggleChips(
      document.getElementById('detail-concs'), concData.concepts || [], inConc,
      async (id, add) => {
        const url = add ? `/api/concepts/${id}/confirm` : `/api/concepts/${id}/reject`;
        await fetch(url, { method: 'POST', body: new URLSearchParams({ db_ids: String(r.db_id) }) });
        toast(add ? 'Reconhecimento confirmado' : 'Reconhecimento descartado', 'success');
      },
    );
  } catch (err) {
    console.warn('detail toggles failed', err);
  }
}

function renderToggleChips(container, items, activeSet, apply) {
  if (!container) return;
  container.innerHTML = '';
  if (!items.length) {
    container.innerHTML = '<span class="detail-empty">(nenhum)</span>';
    return;
  }
  for (const item of items) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'detail-chip' + (activeSet.has(item.id) ? ' active' : '');
    btn.textContent = item.name;
    btn.addEventListener('click', async () => {
      const adding = !btn.classList.contains('active');
      btn.disabled = true;
      try {
        await apply(item.id, adding);
        btn.classList.toggle('active', adding);
      } catch (err) {
        toast('Erro: ' + err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });
    container.appendChild(btn);
  }
}

// ── Faces / persons ────────────────────────────────────────────────────────

async function loadFaces(r, token) {
  const container = document.getElementById('detail-faces');
  if (!container) return;
  let faces = [];
  try {
    const data = await getRecordFaces(r.index);
    faces = data.faces || [];
  } catch (err) {
    console.warn('detail faces failed', err);
  }
  if (token !== state.token) return;

  if (!faces.length) {
    container.innerHTML = '<span class="detail-empty">Nenhum rosto detectado.</span>';
    return;
  }

  const personNames = new Map((r.persons || []).map((p) => [p.id, p.name]));
  container.innerHTML = '';
  for (const face of faces) {
    const item = document.createElement('div');
    item.className = 'detail-face';
    const name = face.person_id != null ? (personNames.get(face.person_id) || '') : '';
    item.innerHTML = `
      <img src="${escapeHtml(faceThumbUrl(face.id))}" alt="rosto">
      <div class="detail-face-info">
        <span class="detail-face-name">${name ? escapeHtml(name) : '<em>Sem nome</em>'}</span>
        <span class="detail-face-actions">
          <button class="btn-link" data-face-action="search">Buscar mídias</button>
          <button class="btn-link" data-face-action="assign">${face.person_id != null ? 'Reatribuir' : 'Atribuir a pessoa'}</button>
        </span>
      </div>`;
    item.querySelector('[data-face-action="search"]').addEventListener('click', () => {
      closeDetail();
      if (face.person_id != null) {
        window.dispatchEvent(new CustomEvent('iris:person', { detail: { personId: face.person_id } }));
      } else {
        window.dispatchEvent(new CustomEvent('iris:face', { detail: { faceId: face.id } }));
      }
    });
    item.querySelector('[data-face-action="assign"]').addEventListener('click', () => assignFace(r, face));
    container.appendChild(item);
  }
}

async function assignFace(r, face) {
  let persons = [];
  try {
    const data = await listPersons();
    persons = data.persons || [];
  } catch (err) {
    toast('Erro ao listar pessoas: ' + err.message, 'error');
    return;
  }
  const choice = await pickPersonModal({
    persons: persons.filter((p) => p.id !== face.person_id),
    title: 'Atribuir rosto a…',
    allowCreate: true,
  });
  if (!choice) return;
  try {
    await assignFacePerson(face.id, choice.createName ? { name: choice.createName } : { personId: choice.personId });
    toast(choice.createName ? `Pessoa criada: ${choice.createName}` : 'Rosto atribuído', 'success');
    // Refresh the aside so names/chips reflect the change.
    const res = await fetch('/api/records/' + r.index);
    if (res.ok) {
      const fresh = await res.json();
      state.records[state.pos] = Object.assign({}, state.records[state.pos], fresh);
      renderAside(fresh, state.token);
    }
  } catch (err) {
    toast('Erro ao atribuir: ' + err.message, 'error');
  }
}

// ── Metadata ───────────────────────────────────────────────────────────────

function metadataRow(label, value) {
  if (value == null || value === '') return '';
  return `<div class="detail-md-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function fullMetadataTable(entries) {
  const rows = entries
    .filter(([, v]) => v != null && typeof v !== 'object')
    .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`)
    .join('');
  return rows ? `<table class="detail-md-table"><tbody>${rows}</tbody></table>` : '';
}

async function loadMetadata(r, btn) {
  btn.disabled = true;
  btn.textContent = 'Carregando…';
  const target = document.getElementById('detail-metadata');
  try {
    const data = await getRecordMetadata(r.index);
    const c = data.curated || {};
    let html = '';

    const gps = c.gps
      ? `<a href="https://www.openstreetmap.org/?mlat=${c.gps.lat}&mlon=${c.gps.lon}#map=16/${c.gps.lat}/${c.gps.lon}" target="_blank" rel="noopener">${escapeHtml(c.location_label || `${c.gps.lat}, ${c.gps.lon}`)}</a>`
      : '';
    const curatedRows = [
      metadataRow('Capturada em', c.captured_at ? c.captured_at.replace('T', ' ') : ''),
      metadataRow('Dispositivo', c.device),
      metadataRow('App de origem', c.source_app),
      gps ? `<div class="detail-md-row"><span>Local</span><strong>${gps}</strong></div>` : '',
    ].filter(Boolean).join('');
    html += curatedRows || '<span class="detail-empty">Sem metadados curados para este arquivo.</span>';

    const full = data.full || {};
    let fullHtml = '';
    if (full.kind === 'image') {
      const base = fullMetadataTable([
        ['Formato', full.format], ['Dimensões', full.width && full.height ? `${full.width} × ${full.height}` : ''], ['Modo', full.mode],
      ]);
      const exif = full.exif ? fullMetadataTable(Object.entries(full.exif)) : '';
      fullHtml = base + exif;
    } else if (full.kind === 'video') {
      const fmt = full.format || {};
      const parts = [fullMetadataTable([
        ['Contêiner', fmt.format_long_name || fmt.format_name],
        ['Duração', fmt.duration ? `${Number(fmt.duration).toFixed(1)} s` : ''],
        ['Bitrate', fmt.bit_rate ? `${Math.round(fmt.bit_rate / 1000)} kb/s` : ''],
      ])];
      for (const s of full.streams || []) {
        parts.push(`<p class="detail-md-stream">${escapeHtml(s.codec_type || 'stream')}</p>`);
        parts.push(fullMetadataTable([
          ['Codec', s.codec_long_name || s.codec_name],
          ['Dimensões', s.width && s.height ? `${s.width} × ${s.height}` : ''],
          ['FPS', s.avg_frame_rate && s.avg_frame_rate !== '0/0' ? s.avg_frame_rate : ''],
          ['Canais', s.channels], ['Amostragem', s.sample_rate ? `${s.sample_rate} Hz` : ''],
        ]));
      }
      fullHtml = parts.join('');
    }
    if (!data.path_exists) {
      fullHtml = '<span class="detail-empty">Arquivo original indisponível — exibindo apenas o que está no catálogo.</span>';
    }
    if (fullHtml) {
      html += `<details class="detail-md-full"><summary>Ver tudo (EXIF/ffprobe)</summary>${fullHtml}</details>`;
    }
    target.innerHTML = html;
    btn.remove();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Exibir metadados';
    toast('Erro ao ler metadados: ' + err.message, 'error');
  }
}

// ── Global wiring ──────────────────────────────────────────────────────────

if (modal) {
  closeBtn.addEventListener('click', closeDetail);
  prevBtn.addEventListener('click', () => nav(-1));
  nextBtn.addEventListener('click', () => nav(1));

  // Click on the media toggles immersive mode; click on the empty stage closes.
  modal.querySelector('.detail-stage').addEventListener('click', (e) => {
    if (e.target.closest('.detail-nav, .detail-close, .detail-counter')) return;
    if (e.target.closest('#detail-media')) {
      modal.classList.toggle('immersive');
    } else {
      closeDetail();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (!state.open) return;
    if (e.target.matches('input, textarea, select, [contenteditable="true"]')) return;
    if (document.querySelector('.app-modal')) return; // a dialog is on top
    if (e.key === 'Escape') {
      e.preventDefault();
      if (modal.classList.contains('immersive')) modal.classList.remove('immersive');
      else closeDetail();
    }
    if (e.key === 'ArrowLeft') { e.preventDefault(); nav(-1); }
    if (e.key === 'ArrowRight') { e.preventDefault(); nav(1); }
  }, true);
}

// Other tabs still open details by index (single-item mode).
window.addEventListener('iris:detail', (e) => {
  if (e.detail && typeof e.detail.index === 'number') openDetailByIndex(e.detail.index);
});

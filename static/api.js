/* ── Iris API client ────────────────────────────────────────────────────────
   All fetch wrappers. Every module imports from here. */

// ── Helpers ──────────────────────────────────────────────────────────────

async function apiGet(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v !== '' && v !== undefined && v !== null) url.searchParams.set(k, v);
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

async function apiPost(path, body) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(body)) {
    if (v !== undefined && v !== null) fd.append(k, v);
  }
  const res = await fetch(path, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

async function apiDelete(path, body = {}) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(body)) {
    if (v !== undefined && v !== null) fd.append(k, v);
  }
  const res = await fetch(path, { method: 'DELETE', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

// ── Info ──────────────────────────────────────────────────────────────────

export async function fetchInfo() {
  return apiGet('/api/info');
}

export async function updateSettings(data) {
  return apiPost('/api/settings', data);
}

export async function reloadBackend() {
  return apiPost('/api/reload', {});
}

export async function browseFilesystem(path = '') {
  return apiGet('/api/filesystem', { path });
}

export async function openFolder(path) {
  return apiPost('/api/open-folder', { path });
}

// ── Records ───────────────────────────────────────────────────────────────

export async function fetchRecords(page = 1, perPage = 24, sortBy = 'importacao', sortAsc = 0, mediaType = 'all', collectionIds = '', conceptIds = '') {
  return apiGet('/api/records', {
    page, per_page: perPage, sort_by: sortBy, sort_asc: sortAsc,
    media_type: mediaType, collection_ids: collectionIds, concept_ids: conceptIds,
  });
}

// ── Search ────────────────────────────────────────────────────────────────

export async function searchText(q, options = {}) {
  return apiGet('/api/search', { q, ...options });
}

export async function searchImage(file, options = {}) {
  const fd = new FormData();
  fd.append('file', file);
  for (const [k, v] of Object.entries(options)) {
    if (v !== '' && v !== undefined && v !== null) fd.append(k, v);
  }
  const res = await fetch('/api/search/image', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export async function searchSimilar(idx, options = {}) {
  return apiGet(`/api/search/similar/${idx}`, options);
}

export async function searchRandom(n = 20) {
  return apiGet('/api/search/random', { n });
}

// ── Collections ───────────────────────────────────────────────────────────

export async function listCollections() {
  return apiGet('/api/collections');
}

export async function createCollection(name) {
  return apiPost('/api/collections', { name });
}

export async function renameCollection(id, name) {
  return apiPost(`/api/collections/${id}/rename`, { name });
}

export async function deleteCollection(id) {
  return apiPost(`/api/collections/${id}/delete`);
}

export async function getCollectionMembers(id) {
  return apiGet(`/api/collections/${id}/members`);
}

export async function addCollectionMembers(id, dbIds) {
  return apiPost(`/api/collections/${id}/members`, { db_ids: dbIds.join(',') });
}

export async function removeCollectionMembers(id, dbIds) {
  return apiPost(`/api/collections/${id}/members/remove`, { db_ids: dbIds.join(',') });
}

export async function getCollectionFilter(ids) {
  return apiGet('/api/collections/filter', { ids: ids.join(',') });
}

// ── Concepts ──────────────────────────────────────────────────────────────

export async function listConcepts() {
  return apiGet('/api/concepts');
}

export async function createConcept(data) {
  return apiPost('/api/concepts', data);
}

export async function createConceptWithReferences(data, files) {
  const fd = new FormData();
  for (const [key, value] of Object.entries(data)) fd.append(key, value);
  Array.from(files).forEach(file => fd.append('files', file));
  const res = await fetch('/api/concepts/with-references', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function updateConcept(id, data) {
  return apiPost(`/api/concepts/${id}/update`, data);
}

export async function deleteConcept(id) {
  return apiPost(`/api/concepts/${id}/delete`);
}

export async function findConceptMatches(id, topK = 80, minScore = 0.65) {
  return apiGet(`/api/concepts/${id}/matches`, { top_k: topK, min_score: minScore });
}

export async function getConceptReferences(id) {
  return apiGet(`/api/concepts/${id}/references`);
}

export async function addConceptReference(conceptId, file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`/api/concepts/${conceptId}/references`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export async function addConceptReferences(conceptId, files) {
  const fd = new FormData();
  Array.from(files).forEach(file => fd.append('files', file));
  const res = await fetch(`/api/concepts/${conceptId}/references/batch`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function deleteConceptReference(conceptId, refId) {
  return apiPost(`/api/concepts/${conceptId}/references/${refId}/delete`);
}

export async function getConceptFilter(ids) {
  return apiGet('/api/concepts/filter', { ids: ids.join(',') });
}

export async function getConceptAssociations(id, page = 1, perPage = 30) {
  return apiGet(`/api/concepts/${id}/associations`, { page, per_page: perPage });
}

export async function confirmConceptMedia(conceptId, dbIds) {
  return apiPost(`/api/concepts/${conceptId}/confirm`, { db_ids: dbIds.join(',') });
}

export async function rejectConceptMedia(conceptId, dbIds) {
  return apiPost(`/api/concepts/${conceptId}/reject`, { db_ids: dbIds.join(',') });
}

// ── Duplicates ────────────────────────────────────────────────────────────

export async function fetchDuplicates(threshold = 0.985, maxNeighbors = 12, minGroupSize = 2) {
  return apiGet('/api/duplicates', {
    threshold,
    max_neighbors: maxNeighbors,
    min_group_size: minGroupSize,
  });
}

// ── System operations ────────────────────────────────────────────────────

export async function startImport(formData) {
  const res = await fetch('/api/import', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function getImportStatus() {
  return apiGet('/api/import/status');
}

export async function getBackupInfo() {
  return apiGet('/api/backup/info');
}

export async function inspectBackup(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/backup/inspect', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function restoreBackup(file) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('confirm', 'true');
  const res = await fetch('/api/backup/restore', { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

// ── Trash ─────────────────────────────────────────────────────────────────

export async function trashRecords(dbIds) {
  return apiPost('/api/trash', { db_ids: dbIds.join(',') });
}

// ── Web enrichment ────────────────────────────────────────────────────────

export async function createEnrichmentJob(dbIds, force = false, config = {}) {
  return apiPost('/api/enrichment/jobs', {
    db_ids: dbIds.join(','),
    force: force ? '1' : '',
    llm_backend: config.backend || '',
    llm_model: config.model || '',
    webchat_target: config.target || '',
    webchat_cdp: config.cdp || '',
    webchat_temporary: config.temporary ? '1' : '0',
  });
}

export async function getEnrichmentJob(jobId) {
  return apiGet(`/api/enrichment/jobs/${jobId}`);
}

export async function listEnrichmentSuggestions(status = 'pending') {
  return apiGet('/api/enrichment/suggestions', { status });
}

export async function applyEnrichmentSuggestion(id, fields) {
  return apiPost(`/api/enrichment/suggestions/${id}/apply`, { fields: fields.join(',') });
}

export async function rejectEnrichmentSuggestion(id) {
  return apiPost(`/api/enrichment/suggestions/${id}/reject`, {});
}

// ── Utilities ─────────────────────────────────────────────────────────────

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

export function mediaUrl(path) {
  if (!path) return '';
  const normalized = String(path).replace(/^\/+/, '');
  return '/media/' + normalized.split('/').map(encodeURIComponent).join('/');
}

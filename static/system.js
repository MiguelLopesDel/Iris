import {
  browseFilesystem,
  fetchInfo,
  getBackupInfo,
  getImportStatus,
  inspectBackup,
  restoreBackup,
  startImport,
  updateSettings,
  escapeHtml,
} from './api.js?v=27';

let initialized = false;
let importPoll = null;
let restoreCandidate = null;
let previousImportStatus = null;

export function initSystem() {
  loadSystemInfo();
  loadBackupInfo();
  pollImportStatus();
  if (initialized) return;
  initialized = true;

  document.getElementById('system-save').addEventListener('click', saveSettings);
  document.getElementById('folder-go').addEventListener('click', () => {
    browseFolder(document.getElementById('import-folder').value);
  });
  document.getElementById('folder-up').addEventListener('click', async () => {
    const data = await browseFilesystem(document.getElementById('import-folder').value);
    browseFolder(data.parent);
  });
  document.getElementById('import-start').addEventListener('click', runImport);
  document.getElementById('backup-library').addEventListener('change', updateBackupLink);
  document.getElementById('restore-file').addEventListener('change', inspectRestore);
  document.getElementById('restore-start').addEventListener('click', runRestore);
  browseFolder(document.getElementById('import-folder').value);
}

async function loadSystemInfo() {
  const health = document.getElementById('system-health');
  try {
    const info = await fetchInfo();
    const dbSelect = document.getElementById('system-db');
    const activePath = info.db_path;
    const databases = [...new Set([activePath, ...(info.databases || [])])];
    dbSelect.innerHTML = databases.map(path =>
      `<option value="${escapeHtml(path)}"${path === activePath ? ' selected' : ''}>${escapeHtml(path)}</option>`
    ).join('');
    document.getElementById('system-media-root').value = info.media_root || 'media';
    document.getElementById('system-model').value = info.model_name || '';
    health.innerHTML = `<strong>${info.total_records}</strong> itens indexados`
      + (info.missing_count ? ` · <span class="danger-text">${info.missing_count} ausentes</span>` : ' · arquivos disponíveis');
  } catch (error) {
    health.textContent = `Erro: ${error.message}`;
  }
}

async function saveSettings() {
  const button = document.getElementById('system-save');
  const health = document.getElementById('system-health');
  button.disabled = true;
  health.textContent = 'Recarregando banco e modelo...';
  try {
    const result = await updateSettings({
      db_path: document.getElementById('system-db').value,
      media_root: document.getElementById('system-media-root').value,
      model_name: document.getElementById('system-model').value,
    });
    health.textContent = `${result.total_records} itens carregados.`;
    window.location.reload();
  } catch (error) {
    health.textContent = `Erro: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function browseFolder(path) {
  const list = document.getElementById('folder-list');
  list.innerHTML = '<span class="filter-empty">Carregando pastas...</span>';
  try {
    const data = await browseFilesystem(path || '');
    document.getElementById('import-folder').value = data.path;
    list.innerHTML = data.directories.length
      ? data.directories.map(directory =>
          `<button class="folder-entry" type="button" data-path="${escapeHtml(directory.path)}">${escapeHtml(directory.name)}</button>`
        ).join('')
      : '<span class="filter-empty">Sem subpastas.</span>';
    list.querySelectorAll('.folder-entry').forEach(button => {
      button.addEventListener('click', () => browseFolder(button.dataset.path));
    });
  } catch (error) {
    list.textContent = `Erro: ${error.message}`;
  }
}

async function runImport() {
  const status = document.getElementById('import-status');
  const button = document.getElementById('import-start');
  const form = new FormData();
  form.append(
    'folder',
    document.getElementById('import-use-folder').checked
      ? document.getElementById('import-folder').value
      : ''
  );
  Array.from(document.getElementById('import-files').files).forEach(file => form.append('files', file));
  form.append('recursive', document.getElementById('import-recursive').checked);
  form.append('library_name', document.getElementById('import-library').value);
  form.append('library_root', document.getElementById('import-library-root').value);
  form.append('copy_to_library', document.getElementById('import-copy').checked);
  form.append('batch_size', document.getElementById('import-batch').value);
  form.append('device', document.getElementById('import-device').value);
  form.append('caption_model', document.getElementById('import-caption').value);
  form.append('whisper_model', document.getElementById('import-whisper').value);

  button.disabled = true;
  status.textContent = 'Enviando importação...';
  try {
    await startImport(form);
    pollImportStatus(true);
  } catch (error) {
    status.textContent = `Erro: ${error.message}`;
    button.disabled = false;
  }
}

async function pollImportStatus(immediate = false) {
  if (importPoll) clearTimeout(importPoll);
  if (!document.getElementById('import-status')) return;
  try {
    const job = await getImportStatus();
    const status = document.getElementById('import-status');
    const button = document.getElementById('import-start');
    const progress = job.total ? ` ${Math.min(job.done + 1, job.total)}/${job.total}` : '';
    status.textContent = job.status === 'idle'
      ? 'Nenhuma importação em andamento.'
      : `${job.message || job.status}${progress}${job.current ? ` · ${job.current}` : ''}`;
    button.disabled = ['queued', 'running'].includes(job.status);
    if (['queued', 'running'].includes(previousImportStatus) && job.status === 'completed') {
      setTimeout(() => window.location.reload(), 800);
    }
    previousImportStatus = job.status;
    if (['queued', 'running'].includes(job.status)) {
      importPoll = setTimeout(pollImportStatus, immediate ? 250 : 1200);
    }
  } catch (error) {
    document.getElementById('import-status').textContent = `Erro: ${error.message}`;
  }
}

async function loadBackupInfo() {
  const container = document.getElementById('backup-info');
  try {
    const info = await getBackupInfo();
    container.innerHTML = metric('Bancos', info.databases)
      + metric('Índices', info.indexes)
      + metric('Mídias', info.library_files)
      + metric('Tamanho', formatBytes(info.database_bytes + info.index_bytes + info.library_bytes));
    updateBackupLink();
  } catch (error) {
    container.textContent = `Erro: ${error.message}`;
  }
}

function metric(label, value) {
  return `<div><span>${escapeHtml(String(label))}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function formatBytes(bytes) {
  if (!bytes) return '0 MB';
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function updateBackupLink() {
  const include = document.getElementById('backup-library').checked;
  document.getElementById('backup-download').href = `/api/backup?include_library=${include}`;
}

async function inspectRestore() {
  const file = document.getElementById('restore-file').files[0];
  const info = document.getElementById('restore-info');
  const button = document.getElementById('restore-start');
  restoreCandidate = null;
  button.disabled = true;
  if (!file) {
    info.textContent = '';
    return;
  }
  info.textContent = 'Inspecionando backup...';
  try {
    const data = await inspectBackup(file);
    restoreCandidate = file;
    button.disabled = false;
    info.textContent = `${data.databases} banco/índice, ${data.library} mídias, ${data.config} configurações.`;
  } catch (error) {
    info.textContent = `Backup inválido: ${error.message}`;
  }
}

async function runRestore() {
  if (!restoreCandidate) return;
  if (!confirm('Restaurar este backup e substituir os arquivos existentes?')) return;
  const info = document.getElementById('restore-info');
  const button = document.getElementById('restore-start');
  button.disabled = true;
  info.textContent = 'Restaurando...';
  try {
    const result = await restoreBackup(restoreCandidate);
    info.textContent = `Restaurado: ${result.databases} bancos/índices e ${result.library} mídias.`;
    window.location.reload();
  } catch (error) {
    info.textContent = `Erro: ${error.message}`;
    button.disabled = false;
  }
}

import {
  browseFilesystem,
  createCollectionFromSuggestion,
  createSnapshot,
  exportMedia,
  fetchInfo,
  getBackupConfig,
  getImportStatus,
  getImportSuggestions,
  listSnapshots,
  reconcileMedia,
  restoreSnapshot,
  saveBackupConfig,
  startImport,
  updateSettings,
  escapeHtml,
} from './api.js?v=29';

let initialized = false;
let importPoll = null;
let previousImportStatus = null;

export function initSystem() {
  loadSystemInfo();
  loadBackupSection();
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
  document.getElementById('backup-config-save').addEventListener('click', saveBackupSettings);
  document.getElementById('snapshot-now').addEventListener('click', runSnapshot);
  document.getElementById('media-reconcile').addEventListener('click', runReconcile);
  document.getElementById('media-export').addEventListener('click', runExport);
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
    const reviewHint = (['completed', 'interrupted'].includes(job.status) && job.quarantined)
      ? ' — veja “Revisão de importação” abaixo.'
      : '';
    status.textContent = job.status === 'idle'
      ? 'Nenhuma importação em andamento.'
      : `${job.message || job.status}${progress}${job.current ? ` · ${job.current}` : ''}${reviewHint}`;
    button.disabled = ['queued', 'running'].includes(job.status);
    // Don't reload on "import anyway" jobs from the review panel — that would
    // interrupt the user clicking through items. Only the main import reloads.
    if (['queued', 'running'].includes(previousImportStatus) && job.status === 'completed' && !job.forced) {
      handleImportComplete(job);
    }
    previousImportStatus = job.status;
    if (['queued', 'running'].includes(job.status)) {
      importPoll = setTimeout(pollImportStatus, immediate ? 250 : 1200);
    }
  } catch (error) {
    document.getElementById('import-status').textContent = `Erro: ${error.message}`;
  }
}

// On a finished import, offer to group the new media into collections based on the
// metadata we read (date / app / location / device). The modal owns the reload so the
// user gets to confirm before the page refreshes.
async function handleImportComplete(job) {
  let suggestions = [];
  try {
    const data = await getImportSuggestions(job.id || '');
    suggestions = data.suggestions || [];
  } catch (_) { /* suggestions are best-effort */ }

  if (suggestions.length) {
    showSuggestionModal(suggestions);
    return;
  }
  if (job.quarantined) window.location.hash = 'system';
  setTimeout(() => window.location.reload(), 800);
}

const DIMENSION_LABELS = {
  date: 'Data', source_app: 'App', location: 'Local', device: 'Dispositivo',
};

function showSuggestionModal(suggestions) {
  const overlay = document.createElement('div');
  overlay.className = 'suggest-modal';
  overlay.innerHTML = `
    <div class="suggest-card">
      <header>
        <h3>Organizar em coleções?</h3>
        <p>Detectamos grupos pelos metadados das mídias importadas. Escolha e edite os nomes.</p>
      </header>
      <div class="suggest-list"></div>
      <footer>
        <span class="suggest-status"></span>
        <div class="suggest-actions">
          <button class="btn btn-subtle" data-act="skip">Agora não</button>
          <button class="btn btn-primary" data-act="create">Criar selecionadas</button>
        </div>
      </footer>
    </div>`;

  const list = overlay.querySelector('.suggest-list');
  suggestions.forEach((sug, i) => {
    const row = document.createElement('label');
    row.className = 'suggest-row';
    row.innerHTML = `
      <input type="checkbox" class="suggest-check" data-i="${i}" checked>
      <span class="suggest-badge">${DIMENSION_LABELS[sug.dimension] || sug.dimension}</span>
      <input type="text" class="suggest-name" data-i="${i}" value="${escapeHtml(sug.name)}">
      <span class="suggest-count">${sug.count} itens</span>`;
    list.appendChild(row);
  });

  const close = () => { overlay.remove(); window.location.reload(); };
  overlay.querySelector('[data-act="skip"]').addEventListener('click', close);
  overlay.querySelector('[data-act="create"]').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const statusEl = overlay.querySelector('.suggest-status');
    let created = 0;
    for (let i = 0; i < suggestions.length; i++) {
      const check = overlay.querySelector(`.suggest-check[data-i="${i}"]`);
      if (!check.checked) continue;
      const name = overlay.querySelector(`.suggest-name[data-i="${i}"]`).value.trim();
      if (!name) continue;
      statusEl.textContent = `Criando “${name}”…`;
      try {
        await createCollectionFromSuggestion(name, suggestions[i].db_ids);
        created += 1;
      } catch (err) {
        statusEl.textContent = `Erro em “${name}”: ${err.message}`;
      }
    }
    statusEl.textContent = `${created} coleção(ões) criada(s). Atualizando…`;
    setTimeout(close, 700);
  });

  document.body.appendChild(overlay);
}

function formatBytes(bytes) {
  if (!bytes) return '0 MB';
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

async function loadBackupSection() {
  try {
    const cfg = await getBackupConfig();
    document.getElementById('backup-dir').value = cfg.backup_dir || '';
    document.getElementById('backup-auto').checked = !!cfg.backup_auto;
    document.getElementById('backup-keep').value = cfg.backup_keep_last || 10;
    const status = document.getElementById('backup-config-status');
    if (!cfg.backup_dir) {
      status.textContent = 'Destino não configurado — configure um caminho externo para ativar os snapshots.';
    } else if (!cfg.dir_ok) {
      status.textContent = `⚠ ${cfg.error || 'Destino indisponível.'}`;
    } else {
      status.textContent = (cfg.warnings && cfg.warnings.length) ? `⚠ ${cfg.warnings[0]}` : 'Destino pronto. ✓';
    }
  } catch (error) {
    document.getElementById('backup-config-status').textContent = `Erro: ${error.message}`;
  }
  await loadSnapshots();
}

async function saveBackupSettings() {
  const status = document.getElementById('backup-config-status');
  status.textContent = 'Salvando...';
  try {
    const res = await saveBackupConfig({
      backupDir: document.getElementById('backup-dir').value.trim(),
      backupAuto: document.getElementById('backup-auto').checked,
      keepLast: Number(document.getElementById('backup-keep').value) || 10,
    });
    status.textContent = (res.warnings && res.warnings.length) ? `Salvo. ⚠ ${res.warnings[0]}` : 'Salvo. ✓';
    await loadSnapshots();
  } catch (error) {
    status.textContent = `Erro: ${error.message}`;
  }
}

async function loadSnapshots() {
  const list = document.getElementById('snapshot-list');
  try {
    const data = await listSnapshots();
    if (!data.configured) {
      list.innerHTML = '<span class="filter-empty">Configure um destino para ver o histórico.</span>';
      return;
    }
    if (!data.snapshots.length) {
      list.innerHTML = '<span class="filter-empty">Nenhum snapshot ainda.</span>';
      return;
    }
    list.innerHTML = data.snapshots.map(snapshotRow).join('');
    list.querySelectorAll('[data-restore]').forEach(btn => {
      btn.addEventListener('click', () => runRestoreSnapshot(btn.dataset.restore, btn.dataset.mode));
    });
  } catch (error) {
    list.innerHTML = `<span class="filter-empty">Erro: ${escapeHtml(error.message)}</span>`;
  }
}

function snapshotRow(s) {
  const when = (s.created_at || '').replace('T', ' ').replace(/(\+.*|Z)$/, '');
  const reason = s.reason ? ` · ${escapeHtml(s.reason)}` : '';
  const count = (s.meme_count != null) ? ` · ${s.meme_count} memes` : '';
  const corrupt = s.corrupt ? ' · ⚠ corrompido' : '';
  return `
    <div class="snapshot-row">
      <div class="snapshot-meta">
        <strong>${escapeHtml(when || s.id)}</strong>
        <span>${escapeHtml(formatBytes(s.size_bytes))}${reason}${count}${corrupt}</span>
      </div>
      <div class="snapshot-actions">
        <button class="btn btn-subtle" data-restore="${escapeHtml(s.id)}" data-mode="overlay">Restaurar</button>
        <button class="btn btn-danger" data-restore="${escapeHtml(s.id)}" data-mode="mirror">Restaurar (espelho)</button>
      </div>
    </div>`;
}

async function runSnapshot() {
  const status = document.getElementById('snapshot-status');
  status.textContent = 'Criando snapshot...';
  try {
    const res = await createSnapshot('manual');
    status.textContent = `Snapshot criado (${formatBytes(res.snapshot.size_bytes)}). ✓`;
    await loadSnapshots();
  } catch (error) {
    status.textContent = `Erro: ${error.message}`;
  }
}

async function runRestoreSnapshot(id, mode) {
  const label = mode === 'mirror'
    ? 'Restaurar em modo ESPELHO apaga bancos/índices órfãos. Continuar?'
    : 'Restaurar este snapshot por cima do estado atual? (um snapshot de segurança será criado antes)';
  if (!confirm(label)) return;
  const status = document.getElementById('snapshot-status');
  status.textContent = 'Restaurando...';
  try {
    await restoreSnapshot(id, mode);
    status.textContent = 'Restaurado. Recarregando...';
    setTimeout(() => window.location.reload(), 800);
  } catch (error) {
    status.textContent = `Erro: ${error.message}`;
  }
}

async function runReconcile() {
  const status = document.getElementById('media-status');
  status.textContent = 'Reconciliando mídia...';
  try {
    const res = await reconcileMedia();
    status.textContent = `Mídia: ${res.present}/${res.total} presentes · ${res.relinked.length} relinkadas · ${res.missing.length} faltando.`;
  } catch (error) {
    status.textContent = `Erro: ${error.message}`;
  }
}

async function runExport() {
  const status = document.getElementById('media-status');
  if (!confirm('Exportar a biblioteca inteira para o disco externo? Pode ser grande e demorado.')) return;
  status.textContent = 'Exportando biblioteca (streaming)...';
  try {
    const res = await exportMedia();
    status.textContent = `Exportado: ${res.files} arquivos (${formatBytes(res.bytes)}) → ${res.path}`;
  } catch (error) {
    status.textContent = `Erro: ${error.message}`;
  }
}

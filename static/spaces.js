/* ── Shared spaces ─────────────────────────────────────────────────────────
   Galleries of their own, shared with other accounts of this server. Nothing
   from the private library shows up here unless someone sends it; removing a
   private copy never removes the space's one. The server enforces every
   permission; the interface only hides what a role cannot do. */

import {
  addSpaceItem,
  addToSpaceAlbum,
  createSpaceAlbum,
  deleteSpaceAlbum,
  listAlbumItems,
  listSpaceAlbums,
  removeFromSpaceAlbum,
  renameSpaceAlbum,
  searchSpace,
  addSpaceMember,
  changeSpaceMemberRole,
  createSpace,
  escapeHtml,
  getSpace,
  getSpaceStorage,
  listSpaceItems,
  listSpaceMembers,
  listSpaces,
  listSpaceTrash,
  removeSpaceItem,
  removeSpaceMember,
  restoreSpaceItem,
  saveSpaceItem,
} from './api.js?v=46';
import { confirmModal, openModal, promptModal, toast } from './ui.js?v=5';

const ROLE_LABELS = { viewer: 'Visualizador', contributor: 'Colaborador', manager: 'Gestor' };
const CAN_ADD = new Set(['contributor', 'manager']);

let initialized = false;
let current = null; // { id, name, role, trashDays }
let itemsCursor = null;
let trashCursor = null;
let album = null; // the album open in the Álbuns tab
let albumCursor = null;

const $ = (id) => document.getElementById(id);

function formatBytes(bytes) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(bytes) || 0;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleDateString('pt-BR', { dateStyle: 'medium' }) : '';
}

// ── List of spaces ──────────────────────────────────────────────────────

async function showList() {
  current = null;
  $('space-view').hidden = true;
  $('spaces-list-view').hidden = false;
  const container = $('spaces-list');
  container.innerHTML = '<p class="filter-empty">Carregando...</p>';
  try {
    const { spaces } = await listSpaces();
    container.innerHTML = spaces.length
      ? spaces.map((space) => `
          <button class="space-card" type="button" data-open-space="${space.id}">
            <b>${escapeHtml(space.name)}</b>
            <span>${ROLE_LABELS[space.role] || space.role}</span>
          </button>`).join('')
      : `<div class="empty-state"><span class="empty-state-icon">◎</span>
           <p>Você ainda não participa de nenhum espaço.</p>
           <small>Crie um e convide outras contas deste servidor.</small></div>`;
  } catch (error) {
    container.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

async function newSpace() {
  const name = await promptModal({
    kicker: 'Compartilhados', title: 'Novo espaço', label: 'Nome',
    placeholder: 'Família, Viagem 2026, Equipe...', confirmLabel: 'Criar',
  });
  if (!name || !name.trim()) return;
  try {
    const { space } = await createSpace(name.trim());
    toast(`Espaço ${space.name} criado. Você é o gestor.`, 'success');
    openSpace(space.id);
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

// ── One space ───────────────────────────────────────────────────────────

async function openSpace(id) {
  try {
    const [{ space }, storage] = await Promise.all([getSpace(id), getSpaceStorage(id)]);
    current = { id: space.id, name: space.name, role: space.role, trashDays: storage.trash_days };
    $('spaces-list-view').hidden = true;
    $('space-view').hidden = false;
    $('space-name').textContent = space.name;
    $('space-search-input').value = '';
    $('space-search-clear').hidden = true;
    $('space-meta').textContent = `Seu papel: ${ROLE_LABELS[space.role] || space.role}`
      + ` · ${formatBytes(storage.used_bytes)} usados de ${formatBytes(storage.quota_bytes)}`;
    document.querySelector('[data-space-view="trash"]').hidden = !CAN_ADD.has(space.role);
    $('space-invite').hidden = space.role !== 'manager';
    $('space-items-hint').textContent = CAN_ADD.has(space.role)
      ? 'Para adicionar, selecione fotos em Fotos e use a ação Espaço.'
      : 'Como visualizador, você pode ver, baixar e salvar cópias na sua biblioteca.';
    showPanel('items');
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
    showList();
  }
}

function showPanel(name) {
  document.querySelectorAll('[data-space-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.spaceView === name);
  });
  document.querySelectorAll('[data-space-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.spacePanel !== name;
  });
  if (name === 'items') loadItems(true);
  if (name === 'albums') showAlbums();
  if (name === 'members') loadMembers();
  if (name === 'trash') loadTrash(true);
}

function itemCard(item, { inAlbum = false } = {}) {
  const name = escapeHtml(item.name);
  const media = item.media_type === 'video'
    ? `<a href="${escapeHtml(item.original_url)}" target="_blank" rel="noopener">
         <img src="${escapeHtml(item.thumbnail_url)}" loading="lazy" alt="${name}">
         <span class="play-overlay" aria-hidden="true">▶</span></a>`
    : `<img src="${escapeHtml(item.thumbnail_url)}" loading="lazy" alt="${name}"
         data-lightbox-src="${escapeHtml(item.original_url)}" data-lightbox-title="${name}">`;
  const author = item.added_by_username ? `por ${escapeHtml(item.added_by_username)}` : 'conta removida';
  return `<div class="media-card" data-space-item="${item.id}">
    <div class="media-card-img">${media}</div>
    <div class="media-card-body">
      <div class="caption" title="${name}">${name}</div>
      <div class="space-item-author">${author} · ${formatDate(item.added_at)}</div>
      <div class="actions">
        <button class="btn" type="button" data-space-save="${item.id}" title="Guardar uma cópia na sua biblioteca">Salvar</button>
        <a class="btn" href="${escapeHtml(item.original_url)}" download="${name}">Baixar</a>
        ${inAlbum
          ? `<button class="btn" type="button" data-album-drop="${item.id}">Tirar do álbum</button>`
          : `${CAN_ADD.has(current?.role) ? `<button class="btn" type="button" data-album-pick="${item.id}">Álbum</button>` : ''}
             ${item.can_remove ? `<button class="btn" type="button" data-space-remove="${item.id}">Remover</button>` : ''}`}
      </div>
    </div>
  </div>`;
}

async function loadItems(reset) {
  if (!current) return;
  const grid = $('space-items');
  if (reset) {
    itemsCursor = null;
    grid.innerHTML = '<p class="filter-empty">Carregando...</p>';
  }
  try {
    const page = await listSpaceItems(current.id, { before: itemsCursor });
    if (reset) grid.innerHTML = '';
    grid.insertAdjacentHTML('beforeend', page.items.map((item) => itemCard(item)).join(''));
    if (reset && !page.items.length) {
      grid.innerHTML = `<div class="empty-state"><span class="empty-state-icon">◎</span>
        <p>Este espaço ainda não tem fotos.</p></div>`;
    }
    itemsCursor = page.next_before;
    $('space-more').hidden = !itemsCursor;
  } catch (error) {
    grid.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

async function saveItem(itemId, button) {
  button.disabled = true;
  try {
    const result = await saveSpaceItem(current.id, itemId);
    toast(result.state === 'duplicate'
      ? 'Esta foto já está na sua biblioteca.'
      : 'Salva na sua biblioteca. Aparece em Fotos depois de processada.', 'success');
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  } finally {
    button.disabled = false;
  }
}

async function removeItem(itemId) {
  const ok = await confirmModal(
    `A foto sai do espaço para todos e fica na lixeira do espaço por ${current.trashDays} dias.`
    + ' As cópias nas bibliotecas pessoais não são afetadas.',
    { kicker: current.name, title: 'Remover do espaço?', confirmLabel: 'Remover', danger: true },
  );
  if (!ok) return;
  try {
    await removeSpaceItem(current.id, itemId);
    document.querySelector(`[data-space-item="${itemId}"]`)?.remove();
    toast('Removida do espaço. Pode ser restaurada pela lixeira.', 'success');
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

// ── Search ──────────────────────────────────────────────────────────────

async function search(event) {
  event.preventDefault();
  const q = $('space-search-input').value.trim();
  if (!q) return loadItems(true);
  const grid = $('space-items');
  grid.innerHTML = '<p class="filter-empty">Buscando...</p>';
  $('space-more').hidden = true;
  $('space-search-clear').hidden = false;
  try {
    const result = await searchSpace(current.id, q);
    grid.innerHTML = result.items.length
      ? result.items.map((item) => itemCard(item)).join('')
      : `<div class="empty-state"><span class="empty-state-icon">⌕</span>
           <p>Nada encontrado neste espaço.</p></div>`;
    $('space-items-hint').textContent = result.semantic
      ? 'Resultados pelo nome, pela descrição e pelo significado da busca.'
      : 'Resultados pelo nome e pela descrição de cada foto.';
  } catch (error) {
    grid.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
  return null;
}

function clearSearch() {
  $('space-search-input').value = '';
  $('space-search-clear').hidden = true;
  loadItems(true);
}

// ── Albums ──────────────────────────────────────────────────────────────

async function showAlbums() {
  album = null;
  $('space-album-view').hidden = true;
  $('space-albums-list-view').hidden = false;
  const container = $('space-albums');
  container.innerHTML = '<p class="filter-empty">Carregando...</p>';
  try {
    const { albums, can_create: canCreate } = await listSpaceAlbums(current.id);
    $('space-album-new').hidden = !canCreate;
    container.innerHTML = albums.length
      ? albums.map((entry) => `
          <button class="space-card space-album-card" type="button" data-open-album="${entry.id}">
            ${entry.cover_url ? `<img src="${escapeHtml(entry.cover_url)}" alt="" loading="lazy">` : '<span class="space-album-empty">◇</span>'}
            <b>${escapeHtml(entry.name)}</b>
            <span>${entry.count} foto(s)</span>
          </button>`).join('')
      : '<p class="filter-empty">Nenhum álbum neste espaço ainda.</p>';
    container.dataset.albums = JSON.stringify(albums);
  } catch (error) {
    container.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

async function newAlbum() {
  const name = await promptModal({ kicker: current.name, title: 'Novo álbum', label: 'Nome', confirmLabel: 'Criar' });
  if (!name || !name.trim()) return null;
  try {
    const { album: created } = await createSpaceAlbum(current.id, name.trim());
    toast(`Álbum ${created.name} criado.`, 'success');
    return created;
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
    return null;
  }
}

function openAlbum(entry) {
  album = entry;
  $('space-albums-list-view').hidden = true;
  $('space-album-view').hidden = false;
  $('space-album-name').textContent = entry.name;
  $('space-album-rename').hidden = !entry.can_edit;
  $('space-album-delete').hidden = !entry.can_edit;
  loadAlbumItems(true);
}

async function loadAlbumItems(reset) {
  const grid = $('space-album-items');
  if (reset) {
    albumCursor = null;
    grid.innerHTML = '<p class="filter-empty">Carregando...</p>';
  }
  try {
    const page = await listAlbumItems(current.id, album.id, { before: albumCursor });
    if (reset) grid.innerHTML = '';
    grid.insertAdjacentHTML('beforeend', page.items.map((item) => itemCard(item, { inAlbum: true })).join(''));
    if (reset && !page.items.length) {
      grid.innerHTML = '<p class="filter-empty">Álbum vazio. Em Fotos, use o botão Álbum de cada foto.</p>';
    }
    albumCursor = page.next_before;
    $('space-album-more').hidden = !albumCursor;
  } catch (error) {
    grid.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

async function renameAlbum() {
  const name = await promptModal({ kicker: current.name, title: 'Renomear álbum', label: 'Nome', value: album.name });
  if (!name || !name.trim()) return;
  try {
    const { album: renamed } = await renameSpaceAlbum(current.id, album.id, name.trim());
    album = renamed;
    $('space-album-name').textContent = renamed.name;
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

async function deleteAlbum() {
  const ok = await confirmModal('As fotos continuam no espaço; só o álbum deixa de existir.',
    { kicker: current.name, title: `Apagar o álbum ${album.name}?`, confirmLabel: 'Apagar', danger: true });
  if (!ok) return;
  try {
    await deleteSpaceAlbum(current.id, album.id);
    toast('Álbum apagado.', 'success');
    showAlbums();
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

async function dropFromAlbum(itemId) {
  try {
    await removeFromSpaceAlbum(current.id, album.id, itemId);
    document.querySelector(`#space-album-items [data-space-item="${itemId}"]`)?.remove();
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

/** Put one photo of the space into an album, creating one if needed. */
async function pickAlbum(itemId) {
  let albums;
  try {
    albums = (await listSpaceAlbums(current.id)).albums;
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
    return;
  }
  const modal = openModal({
    kicker: current.name,
    title: 'Colocar em um álbum',
    body: albums.map((entry) => `<button class="collection-choice" type="button" data-choose-album="${entry.id}">
        <strong>${escapeHtml(entry.name)}</strong><span>${entry.count} foto(s)</span></button>`).join('')
      + '<button class="btn btn-subtle" type="button" data-choose-album="new">+ Novo álbum</button>',
  });
  modal.body.addEventListener('click', async (event) => {
    const choice = event.target.closest('[data-choose-album]')?.dataset.chooseAlbum;
    if (!choice) return;
    modal.close();
    const target = choice === 'new' ? await newAlbum() : albums.find((a) => String(a.id) === choice);
    if (!target) return;
    try {
      const { added } = await addToSpaceAlbum(current.id, target.id, [Number(itemId)]);
      toast(added ? `Colocada em ${target.name}.` : `Já estava em ${target.name}.`, 'success');
    } catch (error) {
      toast(`Erro: ${error.message}`, 'error');
    }
  });
}

// ── Members ─────────────────────────────────────────────────────────────

async function loadMembers() {
  const container = $('space-members');
  container.innerHTML = '<p class="filter-empty">Carregando...</p>';
  try {
    const { members } = await listSpaceMembers(current.id);
    const manager = current.role === 'manager';
    container.innerHTML = members.map((member) => {
      const name = escapeHtml(member.display_name || member.username);
      // A manager edits everyone but themselves here; stepping down or
      // leaving goes through "Sair do espaço" and the last-manager rule.
      const controls = manager && !member.is_you
        ? `<select data-member-role="${member.user_id}" aria-label="Papel de ${name}">
             ${Object.entries(ROLE_LABELS).map(([value, label]) =>
               `<option value="${value}"${value === member.role ? ' selected' : ''}>${label}</option>`).join('')}
           </select>
           <button class="btn btn-subtle" type="button" data-member-remove="${member.user_id}"
             data-member-name="${name}">Remover</button>`
        : `<span class="space-role">${ROLE_LABELS[member.role] || member.role}${member.is_you ? ' · você' : ''}</span>`;
      return `<div class="space-row">
        <span><b>${name}</b><small>${escapeHtml(member.username)}</small></span>
        <span class="space-member-controls">${controls}</span>
      </div>`;
    }).join('');
  } catch (error) {
    container.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

async function setRole(select) {
  try {
    await changeSpaceMemberRole(current.id, select.dataset.memberRole, select.value);
    toast('Papel atualizado.', 'success');
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
  loadMembers();
}

async function removeMember(button) {
  const name = button.dataset.memberName;
  const ok = await confirmModal(
    `${name} perde o acesso ao espaço na hora. As fotos que adicionou continuam no espaço.`,
    { kicker: current.name, title: `Remover ${name}?`, confirmLabel: 'Remover', danger: true },
  );
  if (!ok) return;
  try {
    await removeSpaceMember(current.id, button.dataset.memberRemove);
    toast(`${name} não participa mais de ${current.name}.`, 'success');
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
  loadMembers();
}

async function leave() {
  const ok = await confirmModal(
    'Você perde o acesso a este espaço. As fotos que adicionou continuam nele'
    + ' e as cópias na sua biblioteca não mudam.',
    { kicker: current.name, title: 'Sair do espaço?', confirmLabel: 'Sair', danger: true },
  );
  if (!ok) return;
  try {
    const { members } = await listSpaceMembers(current.id);
    const me = members.find((member) => member.is_you);
    await removeSpaceMember(current.id, me.user_id);
    toast(`Você saiu de ${current.name}.`, 'success');
    showList();
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

async function invite(event) {
  event.preventDefault();
  const username = $('space-invite-username').value.trim();
  if (!username) return;
  try {
    await addSpaceMember(current.id, username, $('space-invite-role').value);
    $('space-invite-username').value = '';
    toast(`${username} agora participa de ${current.name}.`, 'success');
    loadMembers();
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

// ── Trash ───────────────────────────────────────────────────────────────

async function loadTrash(reset) {
  const container = $('space-trash');
  $('space-trash-hint').textContent = current.role === 'manager'
    ? `Fotos removidas do espaço ficam aqui por ${current.trashDays} dias e depois são apagadas.`
    : `Fotos que você adicionou e foram removidas ficam aqui por ${current.trashDays} dias.`;
  if (reset) {
    trashCursor = null;
    container.innerHTML = '<p class="filter-empty">Carregando...</p>';
  }
  try {
    const page = await listSpaceTrash(current.id, { before: trashCursor });
    const rows = page.items.map((item) => `
      <div class="space-row" data-space-trashed="${item.id}">
        <span><b>${escapeHtml(item.name)}</b>
          <small>removida em ${formatDate(item.removed_at)} · apagada em ${formatDate(item.purge_after)}</small></span>
        <button class="btn btn-subtle" type="button" data-space-restore="${item.id}">Restaurar</button>
      </div>`).join('');
    if (reset) container.innerHTML = rows || '<p class="filter-empty">A lixeira está vazia.</p>';
    else container.insertAdjacentHTML('beforeend', rows);
    trashCursor = page.next_before;
    $('space-trash-more').hidden = !trashCursor;
  } catch (error) {
    container.innerHTML = `<p class="danger-text">Erro: ${escapeHtml(error.message)}</p>`;
  }
}

async function restore(itemId) {
  try {
    await restoreSpaceItem(current.id, itemId);
    document.querySelector(`[data-space-trashed="${itemId}"]`)?.remove();
    if (!$('space-trash').querySelector('.space-row')) {
      $('space-trash').innerHTML = '<p class="filter-empty">A lixeira está vazia.</p>';
    }
    toast('Foto de volta ao espaço.', 'success');
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
  }
}

// ── Sending a selection from the private library ────────────────────────

/** Ask which space, then send each private item to it. */
export async function chooseSpaceFor(dbIds) {
  let spaces;
  try {
    spaces = (await listSpaces()).spaces.filter((space) => CAN_ADD.has(space.role));
  } catch (error) {
    toast(`Erro: ${error.message}`, 'error');
    return false;
  }
  if (!spaces.length) {
    openModal({
      kicker: 'Compartilhados', title: 'Nenhum espaço disponível',
      body: '<p>Você só pode enviar fotos a espaços em que é colaborador ou gestor. '
        + 'Crie um em <b>Compartilhados</b> ou peça a um gestor para mudar seu papel.</p>',
      actions: [{ label: 'Fechar', primary: true }],
    });
    return false;
  }
  return new Promise((resolve) => {
    const modal = openModal({
      kicker: 'Compartilhados',
      title: `Enviar ${dbIds.length} item(ns) para um espaço`,
      body: '<p class="section-hint">Cada foto ganha uma cópia no espaço: apagar a sua não apaga a de lá.</p>'
        + spaces.map((space) => `<button class="collection-choice" type="button" data-choose-space="${space.id}">
            <strong>${escapeHtml(space.name)}</strong><span>${ROLE_LABELS[space.role]}</span></button>`).join(''),
      onCancel: () => resolve(false),
    });
    modal.body.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-choose-space]');
      if (!button) return;
      const space = spaces.find((item) => String(item.id) === button.dataset.chooseSpace);
      modal.close();
      resolve(await sendItems(space, dbIds));
    });
  });
}

async function sendItems(space, dbIds) {
  let added = 0;
  let already = 0;
  for (const dbId of dbIds) {
    try {
      const result = await addSpaceItem(space.id, dbId);
      if (result.created) added += 1; else already += 1;
    } catch (error) {
      toast(`Parou em ${added + already} de ${dbIds.length}: ${error.message}`, 'error');
      return added > 0;
    }
  }
  toast(`${added} enviada(s) para ${space.name}`
    + (already ? `, ${already} já estavam lá` : '') + '.', 'success');
  return true;
}

// ── Wiring ──────────────────────────────────────────────────────────────

export function initSpaces() {
  if (!initialized) {
    initialized = true;
    $('btn-new-space').addEventListener('click', newSpace);
    $('space-back').addEventListener('click', showList);
    $('space-more').addEventListener('click', () => loadItems(false));
    $('space-trash-more').addEventListener('click', () => loadTrash(false));
    $('space-invite').addEventListener('submit', invite);
    $('space-leave').addEventListener('click', leave);
    $('space-search').addEventListener('submit', search);
    $('space-search-clear').addEventListener('click', clearSearch);
    $('space-album-new').addEventListener('click', async () => { if (await newAlbum()) showAlbums(); });
    $('space-album-back').addEventListener('click', showAlbums);
    $('space-album-rename').addEventListener('click', renameAlbum);
    $('space-album-delete').addEventListener('click', deleteAlbum);
    $('space-album-more').addEventListener('click', () => loadAlbumItems(false));
    $('space-members').addEventListener('change', (event) => {
      const select = event.target.closest('[data-member-role]');
      if (select) setRole(select);
    });
    $('tab-spaces').addEventListener('click', (event) => {
      const open = event.target.closest('[data-open-space]');
      if (open) return openSpace(open.dataset.openSpace);
      const tab = event.target.closest('[data-space-view]');
      if (tab) return showPanel(tab.dataset.spaceView);
      const save = event.target.closest('[data-space-save]');
      if (save) return saveItem(save.dataset.spaceSave, save);
      const remove = event.target.closest('[data-space-remove]');
      if (remove) return removeItem(remove.dataset.spaceRemove);
      const openA = event.target.closest('[data-open-album]');
      if (openA) {
        const albums = JSON.parse($('space-albums').dataset.albums || '[]');
        const entry = albums.find((a) => String(a.id) === openA.dataset.openAlbum);
        return entry ? openAlbum(entry) : null;
      }
      const pick = event.target.closest('[data-album-pick]');
      if (pick) return pickAlbum(pick.dataset.albumPick);
      const dropA = event.target.closest('[data-album-drop]');
      if (dropA) return dropFromAlbum(dropA.dataset.albumDrop);
      const drop = event.target.closest('[data-member-remove]');
      if (drop) return removeMember(drop);
      const back = event.target.closest('[data-space-restore]');
      if (back) return restore(back.dataset.spaceRestore);
      return null;
    });
  }
  if (current) openSpace(current.id);
  else showList();
}

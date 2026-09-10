/* ── Iris Concepts module ───────────────────────────────────────────────────
   Wizard (simplified): create, edit, auto-match, manage references, delete. */

import {
  addConceptReferences,
  confirmConceptMedia,
  createConceptWithReferences,
  deleteConcept,
  deleteConceptReference,
  escapeHtml,
  findConceptMatches,
  getConceptAssociations,
  getConceptReferences,
  listConcepts,
  rejectConceptMedia,
  updateConcept,
  mediaUrl,
} from './api.js?v=40';
import { confirmModal, toast } from './ui.js?v=1';

var wizardStep = 0;
var wizardData = {};
var conceptsById = new Map();
var associationPages = new Map();
var recognitionLevels = [
  { value: 0.75, label: 'Cuidadoso', help: 'Traz menos sugestões e reduz falsos reconhecimentos.' },
  { value: 0.65, label: 'Equilibrado', help: 'Equilibra precisão e quantidade de sugestões.' },
  { value: 0.55, label: 'Abrangente', help: 'Traz mais possibilidades para você revisar.' },
];

function recognitionLevelOptions(threshold) {
  var closest = recognitionLevels.reduce(function(best, level) {
    return Math.abs(level.value - threshold) < Math.abs(best.value - threshold) ? level : best;
  }, recognitionLevels[1]);
  return recognitionLevels.map(function(level) {
    return '<option value="' + level.value + '"' + (level === closest ? ' selected' : '') + '>'
      + level.label + '</option>';
  }).join('');
}

function recognitionLevelLabel(threshold) {
  return recognitionLevels.reduce(function(best, level) {
    return Math.abs(level.value - threshold) < Math.abs(best.value - threshold) ? level : best;
  }, recognitionLevels[1]).label;
}

export function initConcepts() {
  loadConcepts();
  document.getElementById('btn-new-concept').onclick = startWizard;
}

// ── Wizard ─────────────────────────────────────────────────────────────────

function startWizard() {
  wizardStep = 1;
  wizardData = {
    name: '',
    category: 'outro',
    description: '',
    search_terms: '',
    auto_threshold: 0.65,
    refs: [],
  };
  renderWizard();
}

function renderWizard() {
  var wiz = document.getElementById('concept-wizard');
  wiz.style.display = 'block';
  if (wizardStep === 1) renderWizardStep1(wiz);
  else if (wizardStep === 2) renderWizardStep2(wiz);
  else if (wizardStep === 3) renderWizardStep3(wiz);
  else if (wizardStep === 4) renderWizardStep4(wiz);
}

function renderWizardStep1(wiz) {
  wiz.innerHTML = '<h4>Passo 1: O que o Iris deve reconhecer?</h4>'
    + '<input type="text" id="wiz-name" placeholder="Nome, por exemplo: Frieren" value="' + escapeHtml(wizardData.name) + '" style="width:100%;margin-bottom:8px;">'
    + '<label for="wiz-category">Tipo</label>'
    + '<select id="wiz-category" style="width:100%;margin-bottom:8px;">'
    + ['pessoa','lugar','objeto','personagem','obra','arquetipo','animal','outro'].map(function(c) { return '<option value="' + c + '"' + (wizardData.category === c ? ' selected' : '') + '>' + c + '</option>'; }).join('')
    + '</select>'
    + '<button class="btn" onclick="document.getElementById(\'concept-wizard\').style.display=\'none\'">Cancelar</button> '
    + '<button class="btn" style="background:var(--accent);color:#fff;" onclick="window.__wizNext()">Continuar</button>';
}

window.__wizNext = function() {
  wizardData.name = document.getElementById('wiz-name').value.trim();
  wizardData.category = document.getElementById('wiz-category').value;
  if (!wizardData.name) { toast('Informe o que o Iris deve reconhecer.', 'error'); return; }
  wizardStep = 2;
  renderWizard();
};

function renderWizardStep2(wiz) {
  var questions = wizardQuestions(wizardData.category);
  wiz.innerHTML = '<h4>Passo 2 de 4: Contexto</h4>'
    + questions.map(function(question, index) {
      return '<label>' + escapeHtml(question) + '</label>'
        + '<input type="text" class="wiz-answer" data-question="' + index + '" value="'
        + escapeHtml((wizardData.answers || [])[index] || '') + '">';
    }).join('')
    + '<label>Termos extras de busca:</label><input type="text" id="wiz-terms" value="' + escapeHtml(wizardData.search_terms) + '" placeholder="apelidos, obra, abreviações">'
    + '<label for="wiz-threshold">Precisão do reconhecimento</label>'
    + '<select id="wiz-threshold">' + recognitionLevelOptions(wizardData.auto_threshold) + '</select>'
    + '<div style="margin-top:8px;">'
    + '<button class="btn" onclick="window.__wizBack()">Voltar</button> '
    + '<button class="btn btn-primary" onclick="window.__wizContextNext()">Continuar</button></div>';
}

function wizardQuestions(category) {
  var map = {
    pessoa: ['Apelidos ou nomes alternativos', 'Em que contexto aparece?'],
    lugar: ['País, cidade ou região', 'Nomes alternativos ou abreviações'],
    personagem: ['De qual obra?', 'Características visuais marcantes'],
    obra: ['Tipo (anime, jogo, filme...)', 'Nomes alternativos'],
    arquetipo: ['Que tipo de meme é?', 'Quando se usa este formato'],
    objeto: ['O que é este objeto?', 'Como aparece nas mídias?'],
    animal: ['Espécie ou raça', 'Características visuais marcantes'],
    outro: ['Descrição livre', 'Contexto recorrente'],
  };
  return map[category] || map.outro;
}

window.__wizContextNext = function() {
  wizardData.answers = Array.from(document.querySelectorAll('.wiz-answer')).map(input => input.value.trim());
  wizardData.description = wizardData.answers.filter(Boolean).join(' ');
  wizardData.search_terms = document.getElementById('wiz-terms').value.trim();
  wizardData.auto_threshold = parseFloat(document.getElementById('wiz-threshold').value);
  wizardStep = 3;
  renderWizard();
};

function renderWizardStep3(wiz) {
  wiz.innerHTML = '<h4>Passo 3 de 4: Imagens de referência</h4>'
    + '<p style="font-size:11px;color:var(--text-muted);">Escolha imagens claras e variadas. Elas ensinam ao Iris o que procurar.</p>'
    + '<input type="file" id="wiz-refs" accept="image/*" multiple style="margin-bottom:8px;">'
    + '<div id="wiz-refs-preview" style="display:flex;gap:4px;flex-wrap:wrap;"></div>'
    + '<div style="margin-top:8px;">'
    + '<button class="btn" onclick="window.__wizBack()">Voltar</button> '
    + '<button class="btn btn-primary" onclick="window.__wizRefsNext()">Continuar</button></div>';
  var fileInput = document.getElementById('wiz-refs');
  if (fileInput) fileInput.onchange = function() {
    wizardData.refs = Array.from(this.files);
    var preview = document.getElementById('wiz-refs-preview');
    preview.innerHTML = '';
    wizardData.refs.forEach(function(f) {
      var reader = new FileReader();
      reader.onload = function(e) {
        preview.innerHTML += '<img src="' + e.target.result + '" style="width:60px;height:60px;object-fit:cover;border-radius:4px;">';
      };
      reader.readAsDataURL(f);
    });
  };
}

window.__wizBack = function() {
  wizardStep = Math.max(1, wizardStep - 1);
  renderWizard();
};

window.__wizRefsNext = function() {
  if (!wizardData.refs.length) {
    toast('Adicione pelo menos uma imagem de referência.', 'error');
    return;
  }
  wizardStep = 4;
  renderWizard();
};

function renderWizardStep4(wiz) {
  wiz.innerHTML = '<h4>Passo 4 de 4: Revisão</h4>'
    + '<div class="system-status"><strong>' + escapeHtml(wizardData.name) + '</strong> · '
    + escapeHtml(wizardData.category) + '<br>'
    + escapeHtml(wizardData.description || 'Sem descrição') + '<br>'
    + wizardData.refs.length + ' imagem(ns) de exemplo · modo '
    + recognitionLevelLabel(wizardData.auto_threshold) + '</div>'
    + '<button class="btn" onclick="window.__wizBack()">Voltar</button> '
    + '<button class="btn btn-primary" id="wiz-create-final" onclick="window.__wizCreate()">Ensinar ao Iris</button>';
}

window.__wizCreate = async function() {
  var button = document.getElementById('wiz-create-final');
  if (button) button.disabled = true;
  try {
    await createConceptWithReferences({
      name: wizardData.name,
      category: wizardData.category,
      description: wizardData.description,
      search_terms: wizardData.search_terms,
      auto_threshold: wizardData.auto_threshold,
    }, wizardData.refs);
    document.getElementById('concept-wizard').style.display = 'none';
    wizardStep = 0;
    loadConcepts();
  } catch(err) {
    toast('Erro: ' + err.message, 'error');
    if (button) button.disabled = false;
  }
};

// ── Concept list ────────────────────────────────────────────────────────────

async function loadConcepts() {
  var container = document.getElementById('concepts-tab-list');
  try {
    var data = await listConcepts();
    conceptsById = new Map(data.concepts.map(function(concept) { return [concept.id, concept]; }));
    if (!data.concepts.length) {
      container.innerHTML = '<div class="empty-state"><span class="empty-state-icon">✦</span><p>Você ainda não ensinou nada ao Iris.</p><small>Use “Ensinar algo ao Iris” e adicione imagens de exemplo.</small></div>';
      return;
    }
    container.innerHTML = data.concepts.map(function(c) {
      var safeName = JSON.stringify(c.name);
      var safeDesc = JSON.stringify(c.description || '');
      var safeTerms = JSON.stringify(c.search_terms || '');
      var safeThresh = c.auto_threshold != null ? c.auto_threshold : 0.65;
      return '<div class="detail-panel" style="margin-bottom:8px;" id="conc-panel-' + c.id + '">'
        + '<strong>' + escapeHtml(c.name) + '</strong> <span style="color:var(--text-muted);">(' + escapeHtml(c.category) + ')</span>'
        + ' — ' + (c.assoc_count || 0) + ' item(ns) reconhecido(s), ' + (c.ref_count || 0) + ' exemplo(s)'
        + '<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">'
        + '<button class="btn" onclick="window.__editConcept(' + c.id + ',' + safeName + ',' + safeDesc + ',' + safeTerms + ',' + safeThresh + ')">Editar</button>'
        + '<button class="btn" onclick="window.__autoMatch(' + c.id + ')">Encontrar nas minhas fotos</button>'
        + '<button class="btn" onclick="window.__viewAssoc(' + c.id + ')">Itens reconhecidos</button>'
        + '<button class="btn" onclick="window.__viewRefs(' + c.id + ')">Imagens de exemplo</button>'
        + '<button class="btn btn-danger" onclick="window.__delConcept(' + c.id + ')">Deletar</button>'
        + '</div>'
        + '<div id="conc-extras-' + c.id + '" style="margin-top:8px;"></div>'
        + '</div>';
    }).join('');
  } catch (err) {
    container.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>';
  }
}

// ── Edit concept ────────────────────────────────────────────────────────────

window.__editConcept = function(id, name, desc, terms, threshold) {
  var el = document.getElementById('conc-extras-' + id);
  el.innerHTML = '<div style="margin-top:8px;padding:8px;background:var(--bg-card);border-radius:4px;">'
    + '<input type="text" id="edit-name-' + id + '" value="' + escapeHtml(name) + '" style="width:100%;margin-bottom:4px;" placeholder="Nome">'
    + '<textarea id="edit-desc-' + id + '" rows="2" style="width:100%;margin-bottom:4px;" placeholder="Descricao">' + escapeHtml(desc) + '</textarea>'
    + '<input type="text" id="edit-terms-' + id + '" value="' + escapeHtml(terms) + '" style="width:100%;margin-bottom:4px;" placeholder="Termos de busca">'
    + '<label>Precisão do reconhecimento<select id="edit-thresh-' + id + '">'
    + recognitionLevelOptions(threshold) + '</select></label>'
    + '<div style="margin-top:4px;">'
    + '<button class="btn" onclick="window.__saveConcept(' + id + ')" style="background:var(--accent);color:#fff;">Salvar</button> '
    + '<button class="btn" onclick="document.getElementById(\'conc-extras-' + id + '\').innerHTML=\'\'">Cancelar</button></div></div>';
};

window.__saveConcept = async function(id) {
  try {
    await updateConcept(id, {
      name: document.getElementById('edit-name-' + id).value,
      description: document.getElementById('edit-desc-' + id).value,
      search_terms: document.getElementById('edit-terms-' + id).value,
      auto_threshold: parseFloat(document.getElementById('edit-thresh-' + id).value),
    });
    document.getElementById('conc-extras-' + id).innerHTML = '';
    loadConcepts();
  } catch(err) { toast('Erro: ' + err.message, 'error'); }
};

// ── Recognition suggestions ────────────────────────────────────────────────

window.__autoMatch = async function(conceptId) {
  var el = document.getElementById('conc-extras-' + conceptId);
  var concept = conceptsById.get(conceptId) || {};
  var threshold = concept.auto_threshold != null ? concept.auto_threshold : 0.65;
  el.innerHTML = '<div class="form-grid compact-form">'
    + '<input type="hidden" id="match-topk-' + conceptId + '" value="80">'
    + '<label>Precisão<select id="match-threshold-' + conceptId + '">'
    + recognitionLevelOptions(threshold) + '</select></label>'
    + '</div><button class="btn btn-primary" onclick="window.__runAutoMatch(' + conceptId + ')">Encontrar sugestões</button>';
};

window.__runAutoMatch = async function(conceptId) {
  var el = document.getElementById('conc-extras-' + conceptId);
  var topK = parseInt(document.getElementById('match-topk-' + conceptId).value) || 80;
  var threshold = parseFloat(document.getElementById('match-threshold-' + conceptId).value);
  el.innerHTML = '<p style="color:var(--text-muted);">Procurando nas suas fotos...</p>';
  try {
    var data = await findConceptMatches(conceptId, topK, threshold);
    if (!data.matches.length) {
      el.innerHTML = '<p style="color:var(--text-muted);">Nenhuma correspondência encontrada.</p>';
      return;
    }
    var html = '<p style="font-size:11px;margin-bottom:4px;">' + data.matches.length + ' sugestão(ões) para revisar</p>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;">';
    data.matches.forEach(function(m) {
      var lightboxAttrs = m.media_type === 'image'
        ? ' data-lightbox-src="' + escapeHtml(mediaUrl(m.resolved_path)) + '" data-lightbox-title="' + escapeHtml(m.arquivo || '') + '"'
        : '';
      var thumb = m.thumbnail_url ? '<img src="' + escapeHtml(m.thumbnail_url) + '" loading="lazy"' + lightboxAttrs + ' style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">' : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;">🖼️</div>';
      html += '<div style="font-size:10px;text-align:center;">' + thumb
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml((m.arquivo || '').slice(0, 20)) + '</div>'
        + '<label style="font-size:9px;"><input type="checkbox" class="match-confirm" data-dbid="' + m.db_id + '" checked> Confirmar</label>'
        + '</div>';
    });
    html += '</div>'
      + '<button class="btn" style="margin-top:6px;background:var(--accent);color:#fff;" onclick="window.__applyMatches(' + conceptId + ')">Confirmar seleção</button>';
    el.innerHTML = html;
  } catch(err) { el.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>'; }
};

window.__applyMatches = async function(conceptId) {
  var confirmIds = [];
  var rejectIds = [];
  document.querySelectorAll('.match-confirm').forEach(function(cb) {
    var dbId = parseInt(cb.dataset.dbid);
    if (cb.checked) confirmIds.push(dbId);
    else rejectIds.push(dbId);
  });
  try {
    if (confirmIds.length) await confirmConceptMedia(conceptId, confirmIds);
    if (rejectIds.length) await rejectConceptMedia(conceptId, rejectIds);
    toast(confirmIds.length + ' confirmado(s), ' + rejectIds.length + ' descartado(s)', 'success');
    document.getElementById('conc-extras-' + conceptId).innerHTML = '';
    loadConcepts();
  } catch(err) { toast('Erro: ' + err.message, 'error'); }
};

// ── View associations ───────────────────────────────────────────────────────

window.__viewAssoc = async function(conceptId, page) {
  page = page || associationPages.get(conceptId) || 1;
  associationPages.set(conceptId, page);
  var el = document.getElementById('conc-extras-' + conceptId);
  el.innerHTML = '<p style="color:var(--text-muted);">Carregando...</p>';
  try {
    var data = await getConceptAssociations(conceptId, page, 30);
    if (!data.records.length) {
      el.innerHTML = '<p style="color:var(--text-muted);">Nenhum item reconhecido.</p>';
      return;
    }
    var html = '<p style="font-size:11px;">' + data.total + ' item(ns) reconhecido(s) · página ' + data.page + '/' + data.total_pages + '</p>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;">';
    data.records.forEach(function(m) {
      var lightboxAttrs = m.media_type === 'image'
        ? ' data-lightbox-src="' + escapeHtml(mediaUrl(m.resolved_path)) + '" data-lightbox-title="' + escapeHtml(m.arquivo || '') + '"'
        : '';
      var thumb = m.thumbnail_url ? '<img src="' + escapeHtml(m.thumbnail_url) + '" loading="lazy"' + lightboxAttrs + ' style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">' : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;">🖼️</div>';
      html += '<div style="font-size:10px;text-align:center;">' + thumb
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml((m.arquivo || '').slice(0, 20)) + '</div>'
        + '<label style="font-size:9px;"><input type="checkbox" class="assoc-reject" data-dbid="' + m.db_id + '"> Remover</label></div>';
    });
    html += '</div>'
      + '<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;">'
      + '<button class="btn" ' + (data.page <= 1 ? 'disabled' : '') + ' onclick="window.__viewAssoc(' + conceptId + ',' + (data.page - 1) + ')">Anterior</button>'
      + '<button class="btn" ' + (data.page >= data.total_pages ? 'disabled' : '') + ' onclick="window.__viewAssoc(' + conceptId + ',' + (data.page + 1) + ')">Próxima</button>'
      + '<button class="btn btn-danger" onclick="window.__removeAssoc(' + conceptId + ')">Remover selecionados</button></div>';
    el.innerHTML = html;
  } catch(err) { el.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>'; }
};

window.__removeAssoc = async function(conceptId) {
  var ids = [];
  document.querySelectorAll('.assoc-reject:checked').forEach(function(cb) { ids.push(parseInt(cb.dataset.dbid)); });
  if (!ids.length) { toast('Nenhum item selecionado.', 'info'); return; }
  try {
    await rejectConceptMedia(conceptId, ids);
    toast(ids.length + ' removido(s)', 'success');
    window.__viewAssoc(conceptId);
    loadConcepts();
  } catch(err) { toast('Erro: ' + err.message, 'error'); }
};

// ── View references ─────────────────────────────────────────────────────────

window.__viewRefs = async function(conceptId) {
  var el = document.getElementById('conc-extras-' + conceptId);
  el.innerHTML = '<p style="color:var(--text-muted);">Carregando imagens de exemplo...</p>';
  try {
    var data = await getConceptReferences(conceptId);
    var refs = data.references || [];
    var html = '<p style="font-size:11px;">' + refs.length + ' imagem(ns) de exemplo</p>';
    if (refs.length) {
      html += '<div style="display:flex;gap:6px;flex-wrap:wrap;">';
      refs.forEach(function(ref) {
        html += '<div style="font-size:10px;text-align:center;">'
          + (ref.thumbnail ? '<img src="data:image/jpeg;base64,' + escapeHtml(ref.thumbnail) + '" style="width:80px;height:80px;object-fit:cover;border-radius:4px;">' : '<div style="width:80px;height:80px;background:var(--bg-card);border-radius:4px;">🖼️</div>')
          + '<div>' + escapeHtml((ref.label || '').slice(0, 15)) + '</div>'
          + '<button class="btn btn-danger" style="font-size:9px;padding:1px 4px;" onclick="window.__delRef(' + conceptId + ',' + ref.id + ')">X</button></div>';
      });
      html += '</div>';
    }
    html += '<div style="margin-top:6px;">'
      + '<input type="file" id="ref-upload-' + conceptId + '" accept="image/*" multiple style="font-size:11px;"> '
      + '<button class="btn" onclick="window.__addRef(' + conceptId + ')">Adicionar</button></div>';
    el.innerHTML = html;
  } catch(err) { el.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>'; }
};

window.__addRef = async function(conceptId) {
  var fileInput = document.getElementById('ref-upload-' + conceptId);
  if (!fileInput.files.length) return;
  try {
    await addConceptReferences(conceptId, fileInput.files);
    window.__viewRefs(conceptId);
  } catch(err) { toast('Erro: ' + err.message, 'error'); }
};

window.__delRef = async function(conceptId, refId) {
  try {
    await deleteConceptReference(conceptId, refId);
    window.__viewRefs(conceptId);
    loadConcepts();
  } catch(err) { toast('Erro: ' + err.message, 'error'); }
};

window.__delConcept = async function(id) {
  var ok = await confirmModal('As mídias associadas continuam na biblioteca.', {
    kicker: 'Ensinados ao Iris', title: 'Esquecer este reconhecimento?', confirmLabel: 'Esquecer', danger: true,
  });
  if (!ok) return;
  try {
    await deleteConcept(id);
    toast('Reconhecimento removido', 'success');
    loadConcepts();
  } catch(err) { toast('Erro: ' + err.message, 'error'); }
};

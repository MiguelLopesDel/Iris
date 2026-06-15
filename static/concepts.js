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
} from './api.js?v=26';

var wizardStep = 0;
var wizardData = {};
var conceptsById = new Map();
var associationPages = new Map();

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
  wiz.innerHTML = '<h4>Passo 1: Nome e Categoria</h4>'
    + '<input type="text" id="wiz-name" placeholder="Nome do conceito" value="' + escapeHtml(wizardData.name) + '" style="width:100%;margin-bottom:8px;">'
    + '<select id="wiz-category" style="width:100%;margin-bottom:8px;">'
    + ['pessoa','lugar','objeto','personagem','animal','outro'].map(function(c) { return '<option value="' + c + '"' + (wizardData.category === c ? ' selected' : '') + '>' + c + '</option>'; }).join('')
    + '</select>'
    + '<button class="btn" onclick="document.getElementById(\'concept-wizard\').style.display=\'none\'">Cancelar</button> '
    + '<button class="btn" style="background:var(--accent);color:#fff;" onclick="window.__wizNext()">Continuar</button>';
}

window.__wizNext = function() {
  wizardData.name = document.getElementById('wiz-name').value.trim();
  wizardData.category = document.getElementById('wiz-category').value;
  if (!wizardData.name) { alert('Nome obrigatorio'); return; }
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
    + '<label>Score minimo auto-match:</label><input type="range" id="wiz-threshold" min="0.4" max="0.95" step="0.05" value="' + wizardData.auto_threshold + '" style="width:100%;"> <span id="wiz-threshold-val">' + wizardData.auto_threshold.toFixed(2) + '</span>'
    + '<div style="margin-top:8px;">'
    + '<button class="btn" onclick="window.__wizBack()">Voltar</button> '
    + '<button class="btn btn-primary" onclick="window.__wizContextNext()">Continuar</button></div>';
  var slider = document.getElementById('wiz-threshold');
  if (slider) slider.oninput = function() { document.getElementById('wiz-threshold-val').textContent = this.value; };
}

function wizardQuestions(category) {
  var map = {
    pessoa: ['Apelidos ou nomes alternativos', 'Em que contexto aparece?'],
    lugar: ['País, cidade ou região', 'Nomes alternativos ou abreviações'],
    personagem: ['De qual obra?', 'Características visuais marcantes'],
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
    + '<p style="font-size:11px;color:var(--text-muted);">Escolha imagens claras e variadas. Elas serão processadas ao criar o conceito.</p>'
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
    alert('Adicione pelo menos uma imagem de referência.');
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
    + wizardData.refs.length + ' imagem(ns) de referência · threshold '
    + wizardData.auto_threshold.toFixed(2) + '</div>'
    + '<button class="btn" onclick="window.__wizBack()">Voltar</button> '
    + '<button class="btn btn-primary" id="wiz-create-final" onclick="window.__wizCreate()">Criar conceito</button>';
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
    alert('Erro: ' + err.message);
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
      container.innerHTML = '<p style="color:var(--text-muted);">Nenhum conceito. Crie um acima.</p>';
      return;
    }
    container.innerHTML = data.concepts.map(function(c) {
      var safeName = JSON.stringify(c.name);
      var safeDesc = JSON.stringify(c.description || '');
      var safeTerms = JSON.stringify(c.search_terms || '');
      var safeThresh = c.auto_threshold != null ? c.auto_threshold : 0.65;
      return '<div class="detail-panel" style="margin-bottom:8px;" id="conc-panel-' + c.id + '">'
        + '<strong>' + escapeHtml(c.name) + '</strong> <span style="color:var(--text-muted);">(' + escapeHtml(c.category) + ')</span>'
        + ' — ' + (c.assoc_count || 0) + ' assoc, ' + (c.ref_count || 0) + ' refs'
        + '<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">'
        + '<button class="btn" onclick="window.__editConcept(' + c.id + ',' + safeName + ',' + safeDesc + ',' + safeTerms + ',' + safeThresh + ')">Editar</button>'
        + '<button class="btn" onclick="window.__autoMatch(' + c.id + ')">Auto-match</button>'
        + '<button class="btn" onclick="window.__viewAssoc(' + c.id + ')">Associacoes</button>'
        + '<button class="btn" onclick="window.__viewRefs(' + c.id + ')">Referencias</button>'
        + '<button class="btn btn-danger" onclick="if(confirm(\'Deletar?\'))window.__delConcept(' + c.id + ')">Deletar</button>'
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
    + '<label>Threshold: <input type="range" id="edit-thresh-' + id + '" min="0.4" max="0.95" step="0.05" value="' + threshold + '"> <span id="edit-thresh-val-' + id + '">' + threshold + '</span></label>'
    + '<div style="margin-top:4px;">'
    + '<button class="btn" onclick="window.__saveConcept(' + id + ')" style="background:var(--accent);color:#fff;">Salvar</button> '
    + '<button class="btn" onclick="document.getElementById(\'conc-extras-' + id + '\').innerHTML=\'\'">Cancelar</button></div></div>';
  document.getElementById('edit-thresh-' + id).oninput = function() {
    document.getElementById('edit-thresh-val-' + id).textContent = this.value;
  };
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
  } catch(err) { alert('Erro: ' + err.message); }
};

// ── Auto-match ──────────────────────────────────────────────────────────────

window.__autoMatch = async function(conceptId) {
  var el = document.getElementById('conc-extras-' + conceptId);
  var concept = conceptsById.get(conceptId) || {};
  var threshold = concept.auto_threshold != null ? concept.auto_threshold : 0.65;
  el.innerHTML = '<div class="form-grid compact-form">'
    + '<label>Quantidade máxima<input type="number" id="match-topk-' + conceptId + '" min="10" max="300" value="80"></label>'
    + '<label>Score mínimo<input type="number" id="match-threshold-' + conceptId + '" min="0.4" max="0.95" step="0.01" value="' + threshold + '"></label>'
    + '</div><button class="btn btn-primary" onclick="window.__runAutoMatch(' + conceptId + ')">Buscar candidatos</button>';
};

window.__runAutoMatch = async function(conceptId) {
  var el = document.getElementById('conc-extras-' + conceptId);
  var topK = parseInt(document.getElementById('match-topk-' + conceptId).value) || 80;
  var threshold = parseFloat(document.getElementById('match-threshold-' + conceptId).value);
  el.innerHTML = '<p style="color:var(--text-muted);">Buscando matches...</p>';
  try {
    var data = await findConceptMatches(conceptId, topK, threshold);
    if (!data.matches.length) {
      el.innerHTML = '<p style="color:var(--text-muted);">Nenhum match encontrado.</p>';
      return;
    }
    var html = '<p style="font-size:11px;margin-bottom:4px;">' + data.matches.length + ' candidato(s)</p>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;">';
    data.matches.forEach(function(m) {
      var thumb = m.thumbnail_url ? '<img src="' + escapeHtml(m.thumbnail_url) + '" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">' : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;">🖼️</div>';
      html += '<div style="font-size:10px;text-align:center;">' + thumb
        + '<div>' + (m.score != null ? m.score.toFixed(3) : '?') + '</div>'
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml((m.arquivo || '').slice(0, 20)) + '</div>'
        + '<label style="font-size:9px;"><input type="checkbox" class="match-confirm" data-dbid="' + m.db_id + '" checked> Confirmar</label>'
        + '</div>';
    });
    html += '</div>'
      + '<button class="btn" style="margin-top:6px;background:var(--accent);color:#fff;" onclick="window.__applyMatches(' + conceptId + ')">Aplicar selecao</button>';
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
    alert('Aplicado: ' + confirmIds.length + ' confirmados, ' + rejectIds.length + ' rejeitados');
    document.getElementById('conc-extras-' + conceptId).innerHTML = '';
    loadConcepts();
  } catch(err) { alert('Erro: ' + err.message); }
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
      el.innerHTML = '<p style="color:var(--text-muted);">Nenhuma associacao.</p>';
      return;
    }
    var html = '<p style="font-size:11px;">' + data.total + ' associado(s) · página ' + data.page + '/' + data.total_pages + '</p>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;">';
    data.records.forEach(function(m) {
      var thumb = m.thumbnail_url ? '<img src="' + escapeHtml(m.thumbnail_url) + '" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">' : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;">🖼️</div>';
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
  if (!ids.length) { alert('Nenhum selecionado'); return; }
  try {
    await rejectConceptMedia(conceptId, ids);
    alert(ids.length + ' removido(s)');
    window.__viewAssoc(conceptId);
    loadConcepts();
  } catch(err) { alert('Erro: ' + err.message); }
};

// ── View references ─────────────────────────────────────────────────────────

window.__viewRefs = async function(conceptId) {
  var el = document.getElementById('conc-extras-' + conceptId);
  el.innerHTML = '<p style="color:var(--text-muted);">Carregando referencias...</p>';
  try {
    var data = await getConceptReferences(conceptId);
    var refs = data.references || [];
    var html = '<p style="font-size:11px;">' + refs.length + ' referencia(s)</p>';
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
  } catch(err) { alert('Erro: ' + err.message); }
};

window.__delRef = async function(conceptId, refId) {
  try {
    await deleteConceptReference(conceptId, refId);
    window.__viewRefs(conceptId);
    loadConcepts();
  } catch(err) { alert('Erro: ' + err.message); }
};

window.__delConcept = async function(id) {
  try {
    await deleteConcept(id);
    loadConcepts();
  } catch(err) { alert('Erro: ' + err.message); }
};

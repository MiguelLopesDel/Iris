/* ── Iris Concepts module ───────────────────────────────────────────────────
   Wizard (simplified): create, edit, auto-match, manage references, delete. */

import { listConcepts, createConcept, updateConcept, deleteConcept, findConceptMatches, getConceptReferences, addConceptReference, deleteConceptReference, confirmConceptMedia, rejectConceptMedia } from './api.js';

var wizardStep = 0;
var wizardData = {};

export function initConcepts() {
  loadConcepts();
  document.getElementById('btn-new-concept').onclick = startWizard;
}

// ── Wizard ─────────────────────────────────────────────────────────────────

function startWizard() {
  wizardStep = 1;
  wizardData = { name: '', category: 'outro', description: '', search_terms: '', refs: [] };
  renderWizard();
}

function renderWizard() {
  var wiz = document.getElementById('concept-wizard');
  wiz.style.display = 'block';
  if (wizardStep === 1) renderWizardStep1(wiz);
  else if (wizardStep === 2) renderWizardStep2(wiz);
  else if (wizardStep === 3) renderWizardStep3(wiz);
}

function renderWizardStep1(wiz) {
  wiz.innerHTML = '<h4>Passo 1: Nome e Categoria</h4>'
    + '<input type="text" id="wiz-name" placeholder="Nome do conceito" value="' + wizardData.name + '" style="width:100%;margin-bottom:8px;">'
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
  wiz.innerHTML = '<h4>Passo 2: Descricao e Termos</h4>'
    + '<label>Descricao:</label><textarea id="wiz-desc" rows="2" style="width:100%;margin-bottom:6px;">' + wizardData.description + '</textarea>'
    + '<label>Termos de busca:</label><input type="text" id="wiz-terms" value="' + wizardData.search_terms + '" style="width:100%;margin-bottom:6px;" placeholder="termos separados por virgula">'
    + '<label>Score minimo auto-match:</label><input type="range" id="wiz-threshold" min="0.4" max="0.95" step="0.05" value="0.65" style="width:100%;"> <span id="wiz-threshold-val">0.65</span>'
    + '<div style="margin-top:8px;">'
    + '<button class="btn" onclick="wizardStep=1;renderWizard()">Voltar</button> '
    + '<button class="btn" style="background:var(--accent);color:#fff;" onclick="window.__wizCreate()">Criar conceito</button></div>';
  var slider = document.getElementById('wiz-threshold');
  if (slider) slider.oninput = function() { document.getElementById('wiz-threshold-val').textContent = this.value; };
}

function renderWizardStep3(wiz) {
  // Reference images upload (shown after creation)
  wiz.innerHTML = '<h4>Passo 3: Imagens de referencia</h4>'
    + '<p style="font-size:11px;color:var(--text-muted);">Adicione imagens para melhorar o matching.</p>'
    + '<input type="file" id="wiz-refs" accept="image/*" multiple style="margin-bottom:8px;">'
    + '<div id="wiz-refs-preview" style="display:flex;gap:4px;flex-wrap:wrap;"></div>'
    + '<div style="margin-top:8px;">'
    + '<button class="btn" onclick="loadConcepts();document.getElementById(\'concept-wizard\').style.display=\'none\'">Concluir</button></div>';
  var fileInput = document.getElementById('wiz-refs');
  if (fileInput) fileInput.onchange = function() {
    var preview = document.getElementById('wiz-refs-preview');
    Array.from(this.files).forEach(function(f) {
      var reader = new FileReader();
      reader.onload = function(e) {
        preview.innerHTML += '<img src="' + e.target.result + '" style="width:60px;height:60px;object-fit:cover;border-radius:4px;">';
      };
      reader.readAsDataURL(f);
    });
  };
}

window.__wizCreate = async function() {
  wizardData.description = document.getElementById('wiz-desc').value.trim();
  wizardData.search_terms = document.getElementById('wiz-terms').value.trim();
  wizardData.auto_threshold = parseFloat(document.getElementById('wiz-threshold').value);

  try {
    var result = await createConcept(wizardData);
    wizardData.concept_id = result.id;
    wizardStep = 3;
    renderWizard();
    loadConcepts();
  } catch(err) { alert('Erro: ' + err.message); }
};

// ── Concept list ────────────────────────────────────────────────────────────

async function loadConcepts() {
  var container = document.getElementById('concepts-tab-list');
  try {
    var data = await listConcepts();
    if (!data.concepts.length) {
      container.innerHTML = '<p style="color:var(--text-muted);">Nenhum conceito. Crie um acima.</p>';
      return;
    }
    container.innerHTML = data.concepts.map(function(c) {
      return '<div class="detail-panel" style="margin-bottom:8px;" id="conc-panel-' + c.id + '">'
        + '<strong>' + c.name + '</strong> <span style="color:var(--text-muted);">(' + c.category + ')</span>'
        + ' — ' + (c.assoc_count || 0) + ' assoc, ' + (c.ref_count || 0) + ' refs'
        + '<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">'
        + '<button class="btn" onclick="window.__editConcept(' + c.id + ',\'' + c.name + '\',\'' + (c.description || '') + '\',\'' + (c.search_terms || '') + '\',' + (c.auto_threshold || 0.65) + ')">Editar</button>'
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
    + '<input type="text" id="edit-name-' + id + '" value="' + name + '" style="width:100%;margin-bottom:4px;" placeholder="Nome">'
    + '<textarea id="edit-desc-' + id + '" rows="2" style="width:100%;margin-bottom:4px;" placeholder="Descricao">' + desc + '</textarea>'
    + '<input type="text" id="edit-terms-' + id + '" value="' + terms + '" style="width:100%;margin-bottom:4px;" placeholder="Termos de busca">'
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
  el.innerHTML = '<p style="color:var(--text-muted);">Buscando matches...</p>';
  try {
    var data = await findConceptMatches(conceptId, 30, 0.65);
    if (!data.matches.length) {
      el.innerHTML = '<p style="color:var(--text-muted);">Nenhum match encontrado.</p>';
      return;
    }
    var html = '<p style="font-size:11px;margin-bottom:4px;">' + data.matches.length + ' candidato(s)</p>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;">';
    data.matches.forEach(function(m) {
      var thumb = m.thumbnail_url ? '<img src="' + m.thumbnail_url + '" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">' : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;">🖼️</div>';
      html += '<div style="font-size:10px;text-align:center;">' + thumb
        + '<div>' + m.score.toFixed(3) + '</div>'
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (m.arquivo || '').slice(0, 20) + '</div>'
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

window.__viewAssoc = async function(conceptId) {
  var el = document.getElementById('conc-extras-' + conceptId);
  el.innerHTML = '<p style="color:var(--text-muted);">Carregando...</p>';
  try {
    var data = await findConceptMatches(conceptId, 20, 0.0); // low threshold to get confirmed
    if (!data.matches.length) {
      el.innerHTML = '<p style="color:var(--text-muted);">Nenhuma associacao.</p>';
      return;
    }
    var html = '<p style="font-size:11px;">' + data.matches.length + ' associado(s)</p>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;">';
    data.matches.forEach(function(m) {
      var thumb = m.thumbnail_url ? '<img src="' + m.thumbnail_url + '" loading="lazy" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:4px;">' : '<div style="aspect-ratio:1;background:var(--bg-card);border-radius:4px;">🖼️</div>';
      html += '<div style="font-size:10px;text-align:center;">' + thumb
        + '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (m.arquivo || '').slice(0, 20) + '</div>'
        + '<label style="font-size:9px;"><input type="checkbox" class="assoc-reject" data-dbid="' + m.db_id + '"> Remover</label></div>';
    });
    html += '</div>'
      + '<button class="btn btn-danger" style="margin-top:6px;" onclick="window.__removeAssoc(' + conceptId + ')">Remover selecionados</button>';
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
          + (ref.thumbnail ? '<img src="data:image/jpeg;base64,' + ref.thumbnail + '" style="width:80px;height:80px;object-fit:cover;border-radius:4px;">' : '<div style="width:80px;height:80px;background:var(--bg-card);border-radius:4px;">🖼️</div>')
          + '<div>' + (ref.label || '').slice(0, 15) + '</div>'
          + '<button class="btn btn-danger" style="font-size:9px;padding:1px 4px;" onclick="window.__delRef(' + conceptId + ',' + ref.id + ')">X</button></div>';
      });
      html += '</div>';
    }
    html += '<div style="margin-top:6px;">'
      + '<input type="file" id="ref-upload-' + conceptId + '" accept="image/*" style="font-size:11px;"> '
      + '<button class="btn" onclick="window.__addRef(' + conceptId + ')">Adicionar</button></div>';
    el.innerHTML = html;
  } catch(err) { el.innerHTML = '<p style="color:var(--accent);">Erro: ' + err.message + '</p>'; }
};

window.__addRef = async function(conceptId) {
  var fileInput = document.getElementById('ref-upload-' + conceptId);
  if (!fileInput.files.length) return;
  try {
    await addConceptReference(conceptId, fileInput.files[0]);
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

/* The project wizard: describe → tools → models → begin.
 *
 * Holds no truth of its own. Every edit is PATCHed to the server, which
 * revalidates and returns the project's real state including its blockers,
 * so the button that starts a run is enabled by the same code that would
 * refuse the run — never by a second opinion computed here.
 */

const W = {
  project: null,
  catalog: null,
  step: 'describe',
  pendingUpload: null,   // {module, functions, import_effects}
};

const q = (sel) => document.querySelector(sel);
const qa = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* ---------------------------------------------------------------- projects */

async function refreshProjects() {
  const { projects, uploads_allowed } = await api('/api/projects');
  q('#project-list').innerHTML = projects.length ? projects.map((p) => `
    <div class="project-row">
      <div>
        <div class="p-name">${p.name}</div>
        <div class="p-meta">${p.id} · ${p.tools} tool(s) · ${p.stage} ·
          ${new Date(p.created_at).toLocaleString()}</div>
      </div>
      <div class="p-actions">
        <button data-open="${p.id}">open</button>
        <button data-del="${p.id}" class="row-x">delete</button>
      </div>
    </div>`).join('') : '<p class="muted">No projects yet.</p>';

  qa('[data-open]').forEach((b) =>
    b.addEventListener('click', () => open(b.dataset.open)));
  qa('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm(`Delete ${b.dataset.del}? Its uploaded tool files go too.`)) return;
    await api(`/api/projects/${b.dataset.del}/delete`, { method: 'POST', body: '{}' });
    refreshProjects();
  }));

  if (!uploads_allowed) {
    q('#upload-note').textContent =
      'Tool upload is disabled: this server is not bound to loopback. ' +
      'Uploading a file means executing it in the server process.';
  }
}

async function open(id) {
  try {
    W.project = await api(`/api/projects/${id}`);
  } catch (err) {
    // Deleted in another tab, or its directory removed underneath us. Go
    // back to the list and say so, rather than leaving a wizard open over
    // a project that is not there.
    q('#project-list').innerHTML =
      `<p class="error">Could not open ${id}: ${err.message}</p>`;
    await refreshProjects();
    return;
  }
  q('#projects').hidden = true;
  q('#wizard').hidden = false;
  q('#fingerprint').textContent = W.project.id;
  hydrate();
  goto(W.project.stage === 'done' ? 'ready' : (W.project.stage || 'describe'));
}

async function patch(body) {
  W.project = await api(`/api/projects/${W.project.id}`, {
    method: 'POST', body: JSON.stringify(body),
  });
  render();
  return W.project;
}

/* ------------------------------------------------------------------- steps */

function goto(step) {
  W.step = step;
  qa('#wizard .wizard-step').forEach((el) => { el.hidden = el.dataset.step !== step; });
  qa('.stepbtn').forEach((b) =>
    b.setAttribute('aria-current', String(b.dataset.step === step)));
  render();
}

function hydrate() {
  const p = W.project;
  q('#statement').value = p.statement || '';
  q('#live').checked = !p.offline;
  q('#live-fields').hidden = p.offline;
  q('#target').value = p.target || 'langgraph';
  q('#budget').value = p.budget_usd ?? '';
  if (!p.offline) refreshSecrets();
  q('#repeats').value = p.repeats;
  q('#seeds').value = p.seeds;
  q('#expected').value = p.scenarios[0]?.expected ?? '';

  q('#scratchpad').checked = Boolean(p.scratchpad);
  q('#fault-kinds').innerHTML = Object.entries(W.catalog.fault_kinds).map(
    ([kind, blurb]) => `
    <button type="button" class="chip" data-kind="${kind}" title="${blurb}"
      aria-pressed="${(p.fault_kind || 'wrong_value') === kind}">${kind}</button>`
  ).join('');
  qa('[data-kind]').forEach((chip) => chip.addEventListener('click', async () => {
    const kind = chip.dataset.kind;
    // Radio, not multi-select: the three have different oracles, and a
    // board that averaged them would report one number for three questions.
    qa('[data-kind]').forEach((c) =>
      c.setAttribute('aria-pressed', String(c === chip)));
    const patchBody = { fault_kind: kind };
    // poisoned_memory corrupts the scratchpad, so it implies both the
    // scratchpad and the tool the fault lands on. Making the operator
    // discover that through two blockers would be worse than setting it.
    if (kind === 'poisoned_memory') {
      patchBody.scratchpad = true;
      patchBody.fault_tool = 'recall_note';
      q('#scratchpad').checked = true;
    } else if (W.project.fault_tool === 'recall_note') {
      patchBody.fault_tool = W.project.tools[0]?.name || '';
    }
    await patch(patchBody);
  }));

  q('#prompts').innerHTML = (W.catalog.policies || []).map((name) => `
    <button type="button" class="chip" data-prompt="${name}"
      aria-pressed="${p.prompts.includes(name)}">${name}</button>`).join('');
  qa('#prompts .chip').forEach((chip) => chip.addEventListener('click', () => {
    chip.setAttribute('aria-pressed', chip.getAttribute('aria-pressed') !== 'true');
    patch({ prompts: qa('#prompts .chip[aria-pressed="true"]').map((c) => c.dataset.prompt) });
  }));

  const models = Object.entries(W.catalog.models);
  q('#model-list').innerHTML = models.map(([alias, model]) => {
    const on = p.models.some((m) => m.alias === alias);
    return `<label class="model-row">
      <input type="checkbox" data-model="${alias}" ${on ? 'checked' : ''}>
      <div><div class="m-alias">${alias}</div><div class="m-id">${model}</div></div>
    </label>`;
  }).join('');
  qa('[data-model]').forEach((box) => box.addEventListener('change', () => {
    patch({
      models: qa('[data-model]:checked').map((b) => ({
        alias: b.dataset.model,
        model: W.catalog.models[b.dataset.model],
        // From the catalog, which derives it from the resolved model string.
        // Hardcoding one provider's variable here meant a grid that grew a
        // second provider would check the wrong key.
        credential: W.catalog.model_credentials[b.dataset.model] || null,
      })),
    });
  }));
  render();
}

function render() {
  const p = W.project;
  if (!p) return;
  renderTools();
  renderInputs();
  renderFaultTool();
  renderReview();
  q('#start').disabled = p.blockers.length > 0;
}

/* ------------------------------------------------------------------- tools */

function renderTools() {
  const p = W.project;
  q('#tool-list').innerHTML = p.tools.length ? p.tools.map((t, i) => `
    <div class="tool-card">
      <div class="t-head">
        <span class="t-name">${t.name}</span>
        <span class="t-cost ${t.cost}">${t.cost}</span>
        <span class="t-src">${t.described ? 'described' : `${t.module}:${t.function}`}</span>
        <button class="row-x" data-rm="${i}">remove</button>
      </div>
      ${t.description ? `<div class="t-desc">${t.description}</div>` : ''}
      <div class="t-meta">
        ${t.described ? `${t.rows} row(s) of declared truth`
                      : 'calibrated when the run starts'}
        ${t.credentials.length ? ' · needs ' + t.credentials.map((c) =>
          `<code class="${c.present ? 'ok' : 'missing'}">${c.name}</code>`).join(', ') : ''}
      </div>
    </div>`).join('') : '<p class="muted">No tools yet.</p>';

  qa('[data-rm]').forEach((b) => b.addEventListener('click', () => {
    const tools = p.tools.filter((_, i) => i !== Number(b.dataset.rm));
    patch({ tools: tools.map(toPayload) });
  }));
}

/** The server's view back into the shape it accepts. Credentials travel as
 *  names only — there is no field here for a value, by design. */
function toPayload(t) {
  return {
    name: t.name, description: t.description, cost: t.cost,
    module: t.module, function: t.function,
    arguments: t.arguments,
    requires_credentials: t.credentials.map((c) => c.name),
    table: t.table || (t.described ? t._table : {}) || {},
  };
}

q('#tool-file').addEventListener('change', async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const source = await file.text();
  try {
    W.pendingUpload = await api(`/api/projects/${W.project.id}/upload`, {
      method: 'POST', body: JSON.stringify({ filename: file.name, source }),
    });
  } catch (err) {
    q('#discovered').innerHTML = `<p class="error">${err.message}</p>`;
    return;
  }
  renderDiscovered();
});

function renderDiscovered() {
  const u = W.pendingUpload;
  const warn = u.import_effects.length ? `
    <p class="warn-box">This file runs code at import: ${u.import_effects.join(', ')}.
    Choosing a function from it also runs everything beside it, in this
    process, on this machine.</p>` : '';
  q('#discovered').innerHTML = `${warn}
    <p class="muted">Found in <code>${u.module}</code> — parsed, not imported:</p>
    ${u.functions.map((f, i) => `
      <div class="fn-row">
        <div>
          <code>${f.name}(${f.args.join(', ')})</code>
          ${f.doc ? `<div class="fn-doc">${f.doc}</div>` : ''}
        </div>
        <label class="fn-cost">
          <select data-cost="${i}">
            <option value="negligible">negligible</option>
            <option value="material">material</option>
          </select>
        </label>
        <input type="text" data-cred="${i}" placeholder="env var it needs (optional)">
        <button data-add="${i}" class="ghost">use as tool</button>
      </div>`).join('')}`;

  qa('[data-add]').forEach((b) => b.addEventListener('click', () => {
    const i = Number(b.dataset.add);
    const fn = u.functions[i];
    const cred = q(`[data-cred="${i}"]`).value.trim();
    const tools = W.project.tools.map(toPayload);
    tools.push({
      name: fn.name, description: fn.doc, cost: q(`[data-cost="${i}"]`).value,
      module: u.module, function: fn.name,
      arguments: fn.args.map((a) => ({ name: a, description: '' })),
      requires_credentials: cred ? [cred] : [],
      table: {},
    });
    patch({ tools }).then(() => { q('#discovered').innerHTML = ''; q('#tool-file').value = ''; });
  }));
}

q('#add-described').addEventListener('click', () => {
  const name = q('#d-name').value.trim();
  if (!name) return;
  const table = {};
  q('#d-table').value.split('\n').forEach((line) => {
    const [k, v] = line.split('=').map((s) => (s || '').trim());
    if (!k || v === undefined || v === '') return;
    // The key is the JSON of the argument tuple, matching how the harness
    // keys a calibrated table — one stable string per input.
    table[JSON.stringify([k])] = Number.isNaN(Number(v)) ? v : Number(v);
  });
  const tools = W.project.tools.map(toPayload);
  tools.push({
    name, description: q('#d-desc').value.trim(), cost: q('#d-cost').value,
    module: null, function: null,
    arguments: [{ name: q('#d-arg').value.trim() || 'key', description: '' }],
    requires_credentials: [], table,
  });
  patch({ tools }).then(() => {
    ['#d-name', '#d-desc', '#d-arg', '#d-table'].forEach((s) => { q(s).value = ''; });
  }).catch((err) => { q('#intake-error').hidden = false; q('#intake-error').textContent = err.message; });
});

/* ------------------------------------------------------ inputs & the fault */

function inputs() { return W.project.scenarios[0]?.inputs || []; }

function renderInputs() {
  const rows = inputs();
  q('#inputs-list').innerHTML = rows.length ? rows.map((args, i) => `
    <span class="chip" aria-pressed="true">${args.join(', ')}
      <button class="row-x" data-rmin="${i}">×</button></span>`).join('')
    : '<span class="muted">none yet</span>';
  qa('[data-rmin]').forEach((b) => b.addEventListener('click', () =>
    saveScenario(rows.filter((_, i) => i !== Number(b.dataset.rmin)))));
}

function saveScenario(rows, expected) {
  const current = W.project.scenarios[0] || { id: 'scenario_1' };
  const value = expected === undefined ? current.expected : expected;
  // `Number(null)` is 0, not null. Without this guard, adding an input to an
  // unlabelled project silently labelled it with an expected answer of zero
  // -- every contender then graded against a number nobody supplied.
  const blank = value === '' || value === null || value === undefined;
  return patch({
    scenarios: [{ id: current.id || 'scenario_1', inputs: rows,
                  expected: blank ? null : Number(value) }],
  });
}

q('#add-input').addEventListener('click', () => {
  const raw = q('#new-input').value.trim();
  if (!raw) return;
  saveScenario([...inputs(), raw.split(',').map((s) => s.trim())])
    .then(() => { q('#new-input').value = ''; });
});

q('#expected').addEventListener('change', () => saveScenario(inputs(), q('#expected').value));

function renderFaultTool() {
  const p = W.project;
  q('#kind-note').textContent = W.catalog.fault_kinds[p.fault_kind || 'wrong_value'] || '';

  const sel = q('#fault-tool');
  const names = p.tools.map((t) => t.name)
    .concat(p.scratchpad ? W.catalog.note_tools : []);
  sel.innerHTML = names.map((n) => `<option value="${n}">${n}</option>`).join('')
    || '<option value="">(add a tool first)</option>';
  if (names.includes(p.fault_tool)) sel.value = p.fault_tool;
  else if (names.length && p.fault_tool !== names[0]) patch({ fault_tool: names[0] });
}
q('#fault-tool').addEventListener('change', () => patch({ fault_tool: q('#fault-tool').value }));
q('#scratchpad').addEventListener('change', () =>
  patch({ scratchpad: q('#scratchpad').checked }));

/* ------------------------------------------------------------- credentials */

async function refreshSecrets() {
  const data = await api('/api/secrets');
  W.secrets = data;
  q('#cred-list').innerHTML = data.secrets.map((c) => `
    <div class="cred-row" data-cred-row="${c.name}">
      <code class="${c.present ? 'ok' : 'missing'}">${c.name}</code>
      <span class="cred-state">${c.present
        ? `set · from ${c.source === 'session' ? 'this session' : c.source}`
        : 'not set'}</span>
      ${c.present
        ? `<button type="button" class="row-x" data-clear="${c.name}">forget</button>`
        : `<input type="password" data-secret="${c.name}"
                  placeholder="paste value" autocomplete="off"
                  spellcheck="false">
           <label class="persist"><input type="checkbox" data-persist="${c.name}">
             save to .env</label>
           <button type="button" class="ghost" data-save="${c.name}">set</button>`}
    </div>`).join('') || '<p class="muted">No credentials needed.</p>';

  q('#env-hint').innerHTML =
    `Or put them in a <code>.env</code> — looked for at ` +
    data.env_files.map((f) => `<code>${f}</code>`).join(', ') +
    `. That route never touches the browser at all, and is the one to prefer. ` +
    `<button type="button" id="reload-env" class="ghost">reload .env</button>`;

  qa('[data-save]').forEach((b) => b.addEventListener('click', () => saveSecret(b.dataset.save)));
  qa('[data-secret]').forEach((i) => i.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); saveSecret(i.dataset.secret); }
  }));
  qa('[data-clear]').forEach((b) => b.addEventListener('click', async () => {
    await api('/api/secrets/clear', {
      method: 'POST', body: JSON.stringify({ name: b.dataset.clear }) });
    await refreshSecrets();
    await patch({});            // blockers are recomputed server-side
  }));
  const reload = q('#reload-env');
  if (reload) reload.addEventListener('click', async () => {
    await api('/api/secrets/reload', { method: 'POST', body: '{}' });
    await refreshSecrets();
    await patch({});
  });
}

async function saveSecret(name) {
  const field = q(`[data-secret="${name}"]`);
  const value = field.value;
  if (!value) return;
  // Cleared immediately, and never put anywhere the browser persists:
  // no localStorage, no URL, no form that could be restored on back.
  field.value = '';
  try {
    await api('/api/secrets', {
      method: 'POST',
      body: JSON.stringify({
        name, value, persist: q(`[data-persist="${name}"]`)?.checked || false,
      }),
    });
  } catch (err) {
    q('#intake-error').hidden = false;
    q('#intake-error').textContent = err.message;
    return;
  }
  await refreshSecrets();
  await patch({});              // re-check blockers with the key now present
}

/* -------------------------------------------------------------------- live */

q('#live').addEventListener('change', async () => {
  const live = q('#live').checked;
  q('#live-fields').hidden = !live;
  if (live) await refreshSecrets();
  patch({ offline: !live });
});
q('#target').addEventListener('change', () => patch({ target: q('#target').value }));
q('#budget').addEventListener('change', () =>
  patch({ budget_usd: q('#budget').value === '' ? null : q('#budget').value }));

/* ------------------------------------------------------------------ review */

function renderReview() {
  const p = W.project;
  const sets = 1 + (p.tools.length > 1 ? 1 : 0);          // all, primary-only
  const contenders = p.models.length * p.prompts.length * sets + 1;  // + sentinel
  const runs = contenders * p.repeats * p.seeds * 2;
  const labelled = p.scenarios[0]?.expected !== null
                && p.scenarios[0]?.expected !== undefined;

  q('#estimate').textContent = p.offline ? '' :
    `Rough forecast for this grid: about $${p.estimate_usd.toFixed(2)}. ` +
    'Crude on purpose — the real number is metered from the run and stops it ' +
    'at your ceiling.';

  q('#review').innerHTML = `
    <div class="review-grid">
      <div><span class="k">task</span><span class="v">${p.statement.slice(0, 120) || '—'}</span></div>
      <div><span class="k">tools</span><span class="v">${p.tools.map((t) => t.name).join(', ') || '—'}</span></div>
      <div><span class="k">corrupting</span><span class="v">${p.fault_tool || '—'} · ${p.fault_kind}</span></div>
      <div><span class="k">contenders</span><span class="v">${contenders} → ${runs} runs</span></div>
      <div><span class="k">mode</span><span class="v">${p.offline
        ? 'offline — scripted policies, no spend'
        : `LIVE via ${p.target} · ceiling $${(p.budget_usd || 0).toFixed(2)} · ` +
          `forecast ~$${p.estimate_usd.toFixed(2)}`}</span></div>
      <div><span class="k">oracle</span><span class="v">${labelled
        ? 'your expected answer — full board'
        : 'each contender’s own clean run — propagation is gated, correctness reads n/a'}</span></div>
    </div>
    ${p.material.length ? `<p class="warn-box">
      <strong>${p.material.join(', ')}</strong> ${p.material.length > 1 ? 'are' : 'is'}
      marked material. ${p.material.length > 1 ? 'They are' : 'It is'} called once
      per input during calibration and never again — every run replays those
      values. A contender that re-calls it is counted as doing harm whatever
      its final answer looks like.</p>` : ''}
    ${p.blockers.length ? `<ul class="blockers">${p.blockers.map((b) =>
      `<li>${b}</li>`).join('')}</ul>` : '<p class="ready">Ready.</p>'}`;
}

/* ------------------------------------------------------------------ wiring */

q('#new-project').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const name = q('#project-name').value.trim();
  if (!name) return;
  const p = await api('/api/projects', { method: 'POST', body: JSON.stringify({ name }) });
  q('#project-name').value = '';
  open(p.id);
});

qa('[data-next]').forEach((b) => b.addEventListener('click', () => {
  if (W.step === 'describe') patch({ statement: q('#statement').value, stage: b.dataset.next });
  if (W.step === 'models') patch({ repeats: q('#repeats').value, seeds: q('#seeds').value });
  goto(b.dataset.next);
}));
qa('.stepbtn').forEach((b) => b.addEventListener('click', () => goto(b.dataset.step)));

q('#start').addEventListener('click', async () => {
  const p = W.project;
  if (!p.offline) {
    // An irreversible, outward-facing action with a real cost. It is typed
    // out rather than clicked through, and the ceiling is in the sentence
    // so nobody confirms a number they did not read.
    const want = `spend ${(p.budget_usd || 0).toFixed(2)}`;
    const got = prompt(
      `This runs ${p.target} against real models and spends real money.\n` +
      `Forecast ~$${p.estimate_usd.toFixed(2)}; hard ceiling ` +
      `$${(p.budget_usd || 0).toFixed(2)}.\n\n` +
      `Type "${want}" to confirm.`);
    if (got !== want) return;
  }
  q('#intake-error').hidden = true;
  q('#start').disabled = true;
  try {
    await api(`/api/projects/${W.project.id}/run`, { method: 'POST', body: '{}' });
  } catch (err) {
    q('#intake-error').hidden = false;
    q('#intake-error').textContent = err.message;
    q('#start').disabled = false;
    return;
  }
  q('#wizard').hidden = true;
  window.Arena.begin(W.project.seeds);
});

(async () => {
  W.catalog = await api('/api/catalog');
  await refreshProjects();
})();

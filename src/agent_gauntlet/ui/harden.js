/* Harden an agent: plan (free), then one live round.
 *
 * Draws what /api/harden/* returned and computes nothing. Sizing, the
 * reachable failures, the candidates and the verdict all come from
 * `harden`; an unmeasured figure arrives as null and is printed n/a, never
 * as a number (rule 2).
 */

const H = { catalog: null, plan: null, timer: null };
const hq = (sel) => document.querySelector(sel);

function hesc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* null is "not measured". A formatter that coerced it would print 0%. */
function hpct(x) { return x === null || x === undefined ? 'n/a' : `${Math.round(x * 100)}%`; }
function husd(x) { return x === null || x === undefined ? 'n/a' : `$${x.toFixed(4)}`; }

async function hapi(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function hshow(err) {
  hq('#h-error').hidden = !err;
  hq('#h-error').textContent = err || '';
}

function form() {
  return {
    fixture: hq('#h-fixture').value,
    instructions: hq('#h-instructions').value,
    tools: Array.from(document.querySelectorAll('#h-tools [aria-pressed="true"]'))
      .map((b) => b.dataset.tool),
    model: hq('#h-model').value,
    target: hq('#h-target').value,
    seeds: Number(hq('#h-seeds').value),
    repeats: Number(hq('#h-repeats').value),
  };
}

function renderFixtureNote() {
  const f = H.catalog.fixtures.find((x) => x.key === hq('#h-fixture').value);
  hq('#h-fixture-note').textContent = f
    ? `${f.fault_kind} @ ${f.fault_tool} · the one gate this fault can reach: ${f.gate} · ` +
      `${f.scenarios} scenario(s) · task ${f.fingerprint}`
    : '';
}

function renderPlan(p) {
  const sz = p.sizing;
  const list = (rows) => rows.length
    ? rows.map((r) => `<li><code>${hesc(r.kind)}</code> ${hesc(r.what)}</li>`).join('')
    : '<li class="muted">nothing this task\'s fault can judge — a statement about the registered fault classes only</li>';
  hq('#h-plan-out').innerHTML = `
    <h4 class="sub">The plan</h4>
    <p>baseline <code>${hesc(p.baseline.fingerprint)}</code> · tools
       ${p.baseline.tools.map((t) => `<code>${hesc(t)}</code>`).join(' ')} ·
       gate <code>${hesc(p.gate)}</code> · round ${p.rounds} of ${p.rounds}</p>
    <p class="${sz.enough ? '' : 'error'}">${hesc(sz.reason)}</p>
    <h4 class="sub">Could fail (reachable, not observed — a plan sweeps nothing)</h4>
    <ul>${list(p.reachable)}</ul>
    ${p.elsewhere.length ? `<p class="muted">Other kinds, no candidate built
       (not this task's fault): ${p.elsewhere.map((r) => hesc(r.kind)).join(', ')}</p>` : ''}
    <h4 class="sub">Candidates (structural first — a prompt is not shown to harden anything)</h4>
    ${p.candidates.length ? p.candidates.map((c) => `
      <div class="project-row"><div>
        <div class="p-name">${hesc(c.name)} ${c.structural ? '<span class="chip">structural</span>' : ''}</div>
        <div class="p-meta">${hesc(c.why)}</div>
        ${c.grant.length ? `<div class="p-meta">grants ${c.grant.map(hesc).join(', ')}</div>` : ''}
        ${c.revoke.length ? `<div class="p-meta">revokes ${c.revoke.map(hesc).join(', ')}</div>` : ''}
        ${c.refused ? `<div class="error">not built: ${hesc(c.refused)}</div>`
                    : `<div class="p-meta">fingerprint ${hesc(c.fingerprint)}</div>`}
        ${c.clause ? `<details><summary class="muted">clause appended</summary><pre>${hesc(c.clause)}</pre></details>` : ''}
      </div></div>`).join('') : '<p class="muted">none — nothing in the catalogue answers this</p>'}
    <p class="muted">Offline cannot run an imported agent: a scripted policy
    never reads the prompt, so no offline run could show a change helping.</p>
    <p>A live round is ${p.matrix_runs} matrix runs (plus the baseline sweep).
       No dollar forecast: a hand-written agent has no priced history.</p>
    ${p.live_blockers.length
      ? `<div class="error">Live is blocked:<ul>${p.live_blockers.map((b) => `<li>${hesc(b)}</li>`).join('')}</ul></div>`
      : '<button id="h-run" class="primary">Run the live round</button>'}`;
  const run = hq('#h-run');
  if (run) run.addEventListener('click', startLive);
}

async function plan() {
  hshow('');
  hq('#h-result').innerHTML = '';
  try {
    H.plan = await hapi('/api/harden/plan', form());
    renderPlan(H.plan);
  } catch (err) { hshow(err.message); }
}

async function startLive() {
  const got = prompt(
    `This runs ${H.plan.target} against a real model and spends real money ` +
    `(${H.plan.matrix_runs} matrix runs, no forecast).\n` +
    'There is one round. It is not rerun to green.\n\nType "spend" to confirm.');
  if (got !== 'spend') return;
  hshow('');
  try {
    await hapi('/api/harden/run', { ...form(), confirm: 'spend' });
  } catch (err) { hshow(err.message); return; }
  hq('#h-run').disabled = true;
  hq('#h-result').innerHTML = '<p class="muted">Running one round… this page polls.</p>';
  H.timer = setInterval(pollStatus, 2500);
}

async function pollStatus() {
  const st = await hapi('/api/harden/status');
  if (st.status === 'running') return;
  clearInterval(H.timer);
  if (st.status === 'failed') { hq('#h-result').innerHTML = ''; hshow(st.error); return; }
  if (st.result) renderResult(st.result);
}

function renderResult(r) {
  hq('#h-result').innerHTML = `
    <h4 class="sub">Held out</h4>
    <p>chosen on ${r.chosen_on.map(hesc).join(', ') || 'n/a'} · reported on
       ${r.reported_on.map(hesc).join(', ') || 'n/a'}
       <span class="muted">(the only numbers below)</span></p>
    <table class="board"><thead><tr><th>metric</th><th>before</th><th>after</th><th></th></tr></thead><tbody>
      ${r.changes.map((c) => `<tr><td>${hesc(c.metric)}${c.is_gate ? ' ◆' : ''}</td>
        <td>${hpct(c.before)}</td><td>${hpct(c.after)}</td>
        <td>${hesc(c.verdict)}${c.note ? ` <span class="muted">${hesc(c.note)}</span>` : ''}</td></tr>`).join('')}
      ${r.cost ? `<tr><td>cost/run</td><td>${husd(r.cost.before)}</td><td>${husd(r.cost.after)}</td><td></td></tr>` : ''}
    </tbody></table>
    <p class="verdict ${hesc(r.verdict)}"><strong>${hesc(r.verdict.toUpperCase())}</strong>: ${hesc(r.reason)}</p>
    ${r.exported
      ? `<p>exported <code>${hesc(r.exported)}</code> — review the diff, not the score</p>
         ${r.diff ? `<pre>${hesc(r.diff)}</pre>` : ''}`
      : '<p class="muted">exported nothing — only a shown improvement is handed over.</p>'}
    <p class="muted">Scope: proposals were chosen by a sweep of the same
    scenario they are measured on, from a catalogue of registered fault
    classes. A failure nobody implemented is not found, and not fixed (#18).</p>`;
}

(async () => {
  try { H.catalog = await hapi('/api/harden/catalog'); } catch { return; }
  hq('#h-fixture').innerHTML = H.catalog.fixtures
    .map((f) => `<option value="${hesc(f.key)}">${hesc(f.key)} — ${hesc(f.fault_kind)}</option>`).join('');
  hq('#h-fixture').addEventListener('change', renderFixtureNote);
  renderFixtureNote();
  hq('#h-tools').innerHTML = H.catalog.tools
    .map((t) => `<button type="button" class="chip" data-tool="${hesc(t)}" aria-pressed="false">${hesc(t)}</button>`).join('');
  document.querySelectorAll('#h-tools .chip').forEach((b) => b.addEventListener('click', () =>
    b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')));
  hq('#h-plan').addEventListener('click', plan);
})();

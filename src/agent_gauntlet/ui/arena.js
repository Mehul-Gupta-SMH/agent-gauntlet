/* agent-gauntlet arena.
 *
 * One rule governs this file: the page never computes a score, a rate or a
 * ranking. Everything it draws arrives in an event produced by score_run or
 * board.summarize. Where a value is missing the page prints n/a -- it has no
 * branch that turns an absent measurement into a zero, because inventing one
 * is the failure this whole project is about.
 *
 * The single display-only liberty is PACING. A 300-run matrix finishes in
 * about a second; the queue below replays real events in real order at a
 * watchable rate, and the page says so out loud.
 */

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  catalog: null,
  fighters: new Map(),   // variant id -> {el, stats, factors, ...}
  queue: [],             // real events, awaiting their turn on screen
  cursor: 0,
  playing: false,
  total: 0,
  done: 0,
  selected: null,        // which contender the step log is showing
  logs: new Map(),       // variant id -> step lines
  startedAt: null,
  runsFinishedAt: null,
};

/* ---------------------------------------------------------------- helpers */

const pct = (v) => (v === null || v === undefined ? 'n/a' : `${Math.round(v * 100)}%`);
const num = (v) => (v === null || v === undefined ? 'n/a' : v.toLocaleString());
/** Step counts, not percentages -- and `n/a` when nothing was measured.
 *  An identity formatter here printed the literal string "null" into the
 *  latency column, which is the one thing the whole board refuses to do. */
const steps = (v) => (v === null || v === undefined ? 'n/a' : String(v));

/** A deterministic crest from a hash, so the avatar IS the fingerprint made
 *  visible. Two configurations that differ in substance cannot look alike. */
function crest(seedHex, size = 34) {
  const h = (seedHex || '0').replace(/[^0-9a-f]/gi, '').padEnd(12, '0');
  const n = parseInt(h.slice(0, 8), 16) >>> 0;
  const hue = (parseInt(h.slice(8, 12), 16) % 360);
  const cells = [];
  const grid = 5;
  for (let y = 0; y < grid; y++) {
    for (let x = 0; x < Math.ceil(grid / 2); x++) {
      const bit = (n >>> ((y * 3 + x) % 31)) & 1;
      if (!bit) continue;
      cells.push([x, y]);
      if (x !== grid - 1 - x) cells.push([grid - 1 - x, y]);  // mirrored
    }
  }
  const s = size / grid;
  const off = size / 2;
  // Centred on 0,0 rather than positioned by a transform attribute: the
  // keyframes below animate `transform`, and a CSS transform overrides the
  // SVG presentation attribute outright rather than composing with it --
  // which detached every animating crest from its own avatar.
  const rects = cells
    .map(([x, y]) => `<rect x="${x * s - off}" y="${y * s - off}" width="${s}" height="${s}"/>`)
    .join('');
  return { hue, svg: `<g fill="hsl(${hue} 58% 62%)">${rects}</g>` };
}

function svgEl(name, attrs = {}) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

/* ------------------------------------------------------------------- arena */

function buildArena(variants, faultTool) {
  const fighters = $('#fighters');
  const structure = $('#structure');
  const marks = $('#floor-marks');
  fighters.innerHTML = ''; structure.innerHTML = ''; marks.innerHTML = '';
  state.fighters.clear();

  const cx = 500, cy = 300, rx = 430, ry = 232;

  // Tiered stands, drawn as nested ellipses so the floor reads as sunken.
  for (let i = 5; i >= 1; i--) {
    structure.appendChild(svgEl('ellipse', {
      cx, cy, rx: rx + i * 15, ry: ry + i * 9,
      fill: 'none', stroke: i % 2 ? '#241d16' : '#2c241b', 'stroke-width': 13,
      opacity: 0.35 + i * 0.07,
    }));
  }
  structure.appendChild(svgEl('ellipse', { cx, cy, rx, ry, fill: 'url(#sand)' }));
  structure.appendChild(svgEl('ellipse', {
    cx, cy, rx, ry, fill: 'none', stroke: '#4a3a26', 'stroke-width': 2,
  }));
  structure.appendChild(svgEl('ellipse', {
    cx, cy, rx: rx * 0.55, ry: ry * 0.55, fill: 'none',
    stroke: '#3a2e20', 'stroke-width': 1, 'stroke-dasharray': '5 9',
  }));

  // Contenders on the ring. Position carries no meaning -- deliberately: an
  // arrangement that implied a ranking before a single run would be exactly
  // the kind of decoration this page refuses.
  //
  // Two rings past ten, because a full grid (models x prompts x tool sets,
  // plus the sentinel) crowds one ellipse until the name plates collide and
  // the arena stops being readable.
  const n = variants.length;
  const twoRings = n > 10;
  const outer = twoRings ? Math.ceil(n / 2) : n;
  const scale = n > 16 ? 0.78 : 1;

  variants.forEach((v, i) => {
    const onInner = twoRings && i >= outer;
    const idx = onInner ? i - outer : i;
    const count = onInner ? n - outer : outer;
    const spread = onInner ? 0.42 : 0.82;
    const angle = (-Math.PI / 2) + ((idx + (onInner ? 0.5 : 0)) / count) * Math.PI * 2;
    const x = cx + Math.cos(angle) * rx * spread;
    const y = cy + Math.sin(angle) * ry * spread;

    const g = svgEl('g', { class: 'fighter', transform: `translate(${x} ${y})` });
    g.dataset.variant = v.id;

    // Greyed from the start, before a single run: a contender whose tool set
    // omits the corrupted tool can never be exposed to the fault, so its
    // propagation and detection rates are n/a rather than clean sweeps. The
    // arena says which contenders are not really in this fight, rather than
    // letting them look untouched.
    const grant = state.catalog?.toolsets?.[v.factors.toolset];
    const unarmed = Boolean(faultTool && grant && !grant.includes(faultTool));
    if (unarmed) g.dataset.unarmed = 'true';

    const { hue, svg } = crest(v.fingerprint || v.id, 34 * scale);
    g.appendChild(svgEl('circle', { class: 'halo', r: 30 * scale, fill: 'url(#glow)', opacity: 0 }));
    g.appendChild(svgEl('circle', {
      class: 'ring', r: 24 * scale, fill: '#141009',
      stroke: v.sentinel ? '#4a4038' : `hsl(${hue} 30% 32%)`, 'stroke-width': 1.5,
    }));

    const crestG = svgEl('g', { class: 'crest' });
    crestG.innerHTML = svg;
    g.appendChild(crestG);

    const label = [v.factors.model, v.factors.prompt].filter(Boolean).join(' ');
    const name = svgEl('text', { class: 'name', y: 38 * scale, 'text-anchor': 'middle' });
    name.textContent = label || v.id;
    g.appendChild(name);

    const plate = svgEl('text', { class: 'plate', y: 49 * scale, 'text-anchor': 'middle' });
    plate.textContent = v.factors.toolset || '';
    g.appendChild(plate);

    // Health is running accuracy, and starts empty rather than full: nothing
    // has been measured yet, and a full bar would be a claim.
    const barY = 54 * scale;
    g.appendChild(svgEl('rect', { x: -24, y: barY, width: 48, height: 3, fill: '#241d16', rx: 1.5 }));
    const bar = svgEl('rect', { class: 'hp', x: -24, y: barY, width: 0, height: 3, fill: 'hsl(160 40% 45%)', rx: 1.5 });
    g.appendChild(bar);

    fighters.appendChild(g);
    state.fighters.set(v.id, {
      el: g, bar, x, y, factors: v.factors, sentinel: v.sentinel,
      fingerprint: v.fingerprint, runs: 0, accSum: 0, unarmed,
    });
    state.logs.set(v.id, []);
  });

  buildTabs(variants);
}

function buildTabs(variants) {
  $('#agent-tabs').innerHTML = variants.map((v, i) => `
    <button class="tab" data-variant="${v.id}" aria-pressed="${i === 0}">
      ${[v.factors.model, v.factors.prompt, v.factors.toolset].filter(Boolean).join('/') || v.id}
    </button>`).join('');
  $$('#agent-tabs .tab').forEach((tab) => tab.addEventListener('click', () => {
    $$('#agent-tabs .tab').forEach((t) => t.setAttribute('aria-pressed', 'false'));
    tab.setAttribute('aria-pressed', 'true');
    state.selected = tab.dataset.variant;
    renderSteps();
  }));
  state.selected = variants[0]?.id || null;
}

function setFighterState(id, s, ms = 600) {
  const f = state.fighters.get(id);
  if (!f) return;
  // `fallen` is a verdict about this run; `unarmed` is a fact about the
  // contender's tool grant and outlives every run.
  f.el.dataset.state = s;
  clearTimeout(f.timer);
  if (s !== 'fallen') {
    f.timer = setTimeout(() => { if (f.el.dataset.state === s) f.el.dataset.state = ''; }, ms);
  }
}

function strikeAt(id, text) {
  const f = state.fighters.get(id);
  if (!f) return;
  // Two groups on purpose: the outer one is *positioned* by an SVG
  // attribute, the inner one is *animated* by CSS. Collapsing them would
  // make the animation's transform replace the position and every strike
  // would play in the corner of the arena.
  const g = svgEl('g', { class: 'strike', transform: `translate(${f.x} ${f.y})` });
  const anim = svgEl('g');
  anim.style.animation = 'strike-in .7s ease-out forwards';
  anim.appendChild(svgEl('circle', { r: 26, fill: 'none', stroke: 'var(--blood)', 'stroke-width': 2 }));
  const t = svgEl('text', {
    y: -34, 'text-anchor': 'middle', fill: 'var(--blood)',
    'font-family': 'var(--mono)', 'font-size': 10,
  });
  t.textContent = text;
  anim.appendChild(t);
  g.appendChild(anim);
  $('#strikes').appendChild(g);
  setTimeout(() => g.remove(), 800);
}

/* ------------------------------------------------------------ event replay */

function handle(ev) {
  switch (ev.kind) {
    case 'intake.accepted': {
      $('#fingerprint').textContent = `task ${ev.fingerprint}`;
      state.expected = ev.expected;
      state.total = ev.total_runs;
      $('#progress').textContent = `0 / ${state.total}`;
      renderOracle({ expected: ev.expected, records: ev.records, audited: ev.audited });
      break;
    }
    case 'calibrate.start': {
      tick(`calibrating ${ev.tool}${ev.described ? ' (declared)' : ''} over ${ev.inputs} input(s)`);
      pushCalibration(`${ev.tool} — ${ev.described ? 'declared by you' : 'calling your function'}`
        + (ev.cost === 'material' ? '  [MATERIAL: once, and never again]' : ''));
      break;
    }
    case 'calibrate.done': {
      Object.entries(ev.values).forEach(([k, v]) =>
        pushCalibration(`   ${JSON.parse(k).join(', ')} → ${v}`));
      break;
    }
    case 'project.ready': {
      $('#fingerprint').textContent = `task ${ev.fingerprint}`;
      state.graded = ev.labelled;
      pushCalibration(ev.labelled
        ? 'oracle: your expected answer'
        : "oracle: each contender's own clean run — propagation is gated, "
          + 'correctness reads n/a');
      break;
    }
    case 'matrix.start': {
      // Fires once per seed -- the server runs one matrix per seed so each
      // gets its own base. Build the arena on the first only, or the second
      // seed would wipe the first seed's accumulated state.
      if (!state.fighters.size) {
        buildArena(ev.variants, ev.fault_tool);
        $('#stage').hidden = false;
        $('#technical').hidden = false;
      }
      break;
    }
    case 'run.start': {
      state.currentRun = ev;
      const f = state.fighters.get(ev.variant);
      // Clear a previous run's verdict first. `fallen` is sticky by design
      // -- it has no timeout -- so without this the arena would show a
      // variant permanently down after one bad run out of dozens, which is
      // a claim about the variant that no single run supports.
      if (f) { f.el.dataset.state = ''; setFighterState(ev.variant, 'acting', 1200); }
      pushLog(ev.variant, {
        cls: 'head',
        text: `── ${ev.scenario} · repeat ${ev.repeat} · ${ev.condition} · seed ${ev.seed}`,
      });
      if (ev.faults.length) {
        ev.faults.forEach((fl) => pushLog(ev.variant, {
          cls: 'head',
          text: `   fault armed: ${fl.tool}(${fl.target}) ${fl.true} → ${fl.corrupt}`,
        }));
      }
      renderOracle({
        expected: ev.expected,
        credulous: ev.faults.length ? ev.credulous : undefined,
        faults: ev.faults,
        condition: ev.condition,
        run: `${label(ev.variant)} · ${ev.condition} · repeat ${ev.repeat}`,
      });
      tick(`${label(ev.variant)} · ${ev.condition} · ${ev.scenario}`);
      break;
    }
    case 'tool.call': {
      const v = ev.run.split('|')[0];
      pushLog(v, {
        cls: `step${ev.faulted ? ' faulted' : ''}`,
        step: ev.step, tool: ev.tool, key: ev.key,
        value: Array.isArray(ev.result) ? `[${ev.result.length} ids]` : ev.result,
        faulted: ev.faulted, cost: ev.cost, redundant: ev.redundant,
      });
      if (ev.faulted) { setFighterState(v, 'struck', 700); strikeAt(v, String(ev.result)); }
      else setFighterState(v, 'acting', 500);
      if (ev.redundant) strikeAt(v, 'second inquiry');
      break;
    }
    case 'run.end': {
      state.done += 1;
      $('#progress').textContent = `${state.done} / ${state.total || '?'}`;
      const f = state.fighters.get(ev.variant);
      if (f) {
        f.runs += 1; f.accSum += ev.accuracy;
        f.bar.setAttribute('width', (f.accSum / f.runs) * 48);
        f.bar.setAttribute('fill', ev.propagated ? 'hsl(8 62% 52%)' : 'hsl(160 40% 45%)');
      }
      if (ev.propagated) setFighterState(ev.variant, 'fallen');
      else if (ev.repaired) setFighterState(ev.variant, 'parried', 900);
      pushLog(ev.variant, {
        cls: 'result',
        text: `   → answered ${num(ev.answer)}${ev.flagged ? ' (flagged anomaly)' : ''}` +
              ` · ${ev.outcome}${ev.error ? ` · ERROR ${ev.error}` : ''}` +
              (ev.redundant_material ? ` · ${ev.redundant_material} redundant material call(s)` : ''),
      });
      break;
    }
    case 'board': {
      renderBoard(ev.rows, ev.winner, ev.errored, ev.total, ev.winner_reason);
      tick(`done — ${ev.total} runs scored`);
      break;
    }
    case 'failed': {
      $('#intake-error').hidden = false;
      $('#intake-error').textContent = ev.error;
      $('#wizard').hidden = false;
      $('#start').disabled = false;
      break;
    }
  }
}

const label = (vid) => {
  const f = state.fighters.get(vid);
  if (!f) return vid;
  return [f.factors.model, f.factors.prompt, f.factors.toolset].filter(Boolean).join('/');
};

function tick(text) { $('#ticker-text').textContent = text; }

/** Calibration output goes to every contender's log, because it is the
 *  world all of them share -- not one contender's private history. */
function pushCalibration(text) {
  const box = $('#steps');
  const div = document.createElement('div');
  div.className = 'step head';
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function pushLog(variant, entry) {
  const lines = state.logs.get(variant);
  if (!lines) return;
  lines.push(entry);
  if (lines.length > 4000) lines.splice(0, lines.length - 4000);
  if (variant === state.selected) appendStep(entry);
}

function stepNode(e) {
  const div = document.createElement('div');
  div.className = e.cls || 'step';
  if (e.text !== undefined) { div.textContent = e.text; return div; }
  const tags = [];
  if (e.cost === 'material') tags.push('<span class="tag material">material</span>');
  if (e.redundant) tags.push('<span class="tag redundant">redundant</span>');
  div.innerHTML =
    `<span class="n">${e.step}</span>` +
    `<span class="tool">${e.tool}</span>` +
    `<span class="arg">(${e.key})</span>` +
    `<span class="val">→ ${e.value}</span>` +
    (e.faulted ? '<span class="tag material">injected</span>' : '') +
    tags.join('');
  return div;
}

function appendStep(e) {
  const box = $('#steps');
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.appendChild(stepNode(e));
  while (box.childElementCount > 1200) box.firstElementChild.remove();
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderSteps() {
  const box = $('#steps');
  box.innerHTML = '';
  (state.logs.get(state.selected) || []).slice(-1200).forEach((e) => box.appendChild(stepNode(e)));
  box.scrollTop = box.scrollHeight;
}

function renderOracle({ expected, credulous, faults, records, audited, condition, run }) {
  const el = $('#oracle');
  // Naming the run matters: this panel follows the run happening *now*,
  // while the step log follows whichever contender is selected. Side by
  // side and unlabelled they read as two views of one run that disagree.
  const parts = [
    run ? `<p class="oracle-run">${run}</p>` : '',
    `<p class="muted">The harness injected the fault, so it knows both the
     truth and the figure a credulous agent would report. Propagation is
     decided by comparison — never by a judge.</p>`,
  ];
  parts.push('<div class="oracle-figures">');
  // Under an unlabelled project the figure below is the sum of the
  // calibrated table, not a truth anyone declared -- the oracle is each
  // contender's own clean run. Saying "truth" over it would be the board's
  // own rule broken in the panel that explains the board.
  parts.push(state.graded === false
    ? `<div class="fig"><div class="k">no declared truth</div>` +
      `<div class="v">n/a</div></div>`
    : `<div class="fig truth"><div class="k">truth</div><div class="v">${num(expected)}</div></div>`);
  if (credulous !== undefined) {
    parts.push(state.graded === false
      ? `<div class="fig lie"><div class="k">oracle</div>` +
        `<div class="v">clean twin</div></div>`
      : `<div class="fig lie"><div class="k">credulous figure</div><div class="v">${num(credulous)}</div></div>`);
    parts.push(`<div class="fig"><div class="k">injected delta</div><div class="v">${num(credulous - expected)}</div></div>`);
  } else if (condition === 'clean') {
    // No fault was armed, so there is no figure a credulous agent would
    // reach. n/a, not a zero.
    parts.push(`<div class="fig lie"><div class="k">credulous figure</div><div class="v">n/a</div></div>`);
    parts.push(`<div class="fig"><div class="k">delta</div><div class="v">n/a</div></div>`);
  }
  parts.push('</div>');
  if (condition === 'clean') {
    parts.push('<div class="muted" style="margin-top:.7rem">clean baseline — '
      + 'nothing injected. Robustness is degradation from a contender\'s own '
      + 'clean run, so every faulted run needs this twin.</div>');
  } else if (faults && faults.length) {
    parts.push('<div class="muted" style="margin-top:.7rem">' + faults.map((f) =>
      `${f.tool}(${f.target}) returns <code>${f.corrupt}</code> instead of <code>${f.true}</code>`
    ).join('<br>') + '</div>');
  } else if (audited) {
    parts.push(`<div class="muted" style="margin-top:.7rem">audited: ${audited.join(', ') || '—'}</div>`);
  }
  el.innerHTML = parts.join('');
}

function renderBoard(rows, winner, errored, total, reason) {
  const tbody = $('#board tbody');
  tbody.innerHTML = rows.map((r) => {
    const cls = [r.gated ? 'gated' : '', r.is_sentinel ? 'sentinel' : ''].join(' ').trim();
    const flag = r.propagation_unmeasured ? 'GATED: propagation not measurable'
               : r.gated ? 'GATED: propagated' : '';
    // Short-circuits on the VALUE, not just the class. The first version
    // set the `na` class correctly and still called the formatter on null,
    // so a custom formatter threw and took the whole board down with it.
    const cell = (v, fmt = pct) =>
      (v === null || v === undefined)
        ? '<td class="na">n/a</td>'
        : `<td>${fmt(v)}</td>`;
    return `<tr class="${cls}">
      <td data-flag="${flag}">${r.label}</td>
      <td>${r.n_runs}</td>
      ${cell(r.quality)}
      ${cell(r.accuracy, (v) => v.toFixed(2))}
      ${cell(r.clean_quality)}${cell(r.faulted_quality)}
      ${cell(r.propagation_rate)}${cell(r.detection_rate)}${cell(r.repair_rate)}
      ${cell(r.false_alarm_rate)}
      ${cell(r.median_detect_latency, steps)}
      <td class="${r.redundant_material_calls ? 'harm' : 'na'}">${r.redundant_material_calls || '—'}</td>
    </tr>`;
  }).join('');

  const w = $('#winner');
  w.hidden = false;
  if (!winner) {
    w.className = 'winner';
    w.innerHTML = `<h3>no validated winner</h3>
      <p class="muted">${reason || 'No held-out winner could be formed.'}
      The top of the ranking is deliberately not reported here: the score that
      selected it is optimistically biased by that selection (#20).</p>`;
  } else {
    w.className = `winner${winner.held_up ? '' : ' collapsed'}`;
    w.innerHTML = `<h3>${winner.held_up ? 'winner (held out)' : 'the winner did not hold up'}</h3>
      <div class="vid">${winner.variant_id}</div>
      <div class="scores">
        selected on held-in seeds → ${winner.selection_score.toFixed(3)} <em>(not the number to quote)</em><br>
        held-out → <strong>${winner.holdout_score.toFixed(3)}</strong> ·
        rank ${winner.holdout_rank} of ${winner.n_candidates}
      </div>
      ${winner.held_up ? '' : `<p class="muted" style="margin-top:.5rem">
        Picked on one set of replications and not top on fresh ones — what a
        search fitting noise looks like. Do not ship this config on this run.</p>`}`;
  }

  if (errored) {
    $('#board').insertAdjacentHTML('afterend',
      `<p class="muted">${errored} of ${total} runs never produced an answer and are
       excluded from every rate above — an agent that never got to answer is
       not an agent that answered badly.</p>`);
  }
}

/* ------------------------------------------------------------------ driver */

function drain() {
  if (!state.queue.length) { state.playing = false; return; }
  state.playing = true;
  const speed = parseInt($('#speed').value, 10);
  // Tool calls are the texture; run boundaries deserve a beat. Both are
  // real events -- only the gap between them is chosen.
  const ev = state.queue.shift();
  handle(ev);
  const base = ev.kind === 'run.start' ? 90 : ev.kind === 'run.end' ? 70 : 34;
  setTimeout(drain, Math.max(4, base / speed));
}

async function poll() {
  const res = await fetch(`/api/events?since=${state.cursor}`);
  const data = await res.json();
  state.cursor = data.cursor;
  if (data.events.length) {
    state.queue.push(...data.events);
    if (!state.playing) drain();
  }
  const pill = $('#status-pill');
  pill.textContent = data.status;
  pill.className = `pill ${data.status}`;

  if (data.status === 'running' || state.queue.length) {
    setTimeout(poll, 220);
  } else if (data.status === 'done') {
    if (!state.runsFinishedAt) {
      state.runsFinishedAt = Date.now();
      $('#wallclock').textContent =
        `The matrix itself finished in ${((state.runsFinishedAt - state.startedAt) / 1000).toFixed(1)}s.`;
    }
  } else if (data.status === 'failed') {
    $('#intake-error').hidden = false;
    $('#intake-error').textContent = data.error || 'run failed';
    $('#wizard').hidden = false;
    $('#start').disabled = false;
  }
}

/** Called by the wizard once the server has accepted a run. */
function begin(seeds) {
  state.seeds = seeds;
  state.done = 0; state.cursor = 0; state.queue = []; state.total = 0;
  state.startedAt = Date.now(); state.runsFinishedAt = null;
  state.fighters.clear(); state.logs.clear();
  poll();
}

window.Arena = { begin, state };

$('#speed').addEventListener('input', () => {
  $('#speed-label').textContent = `${$('#speed').value}\u00d7`;
});

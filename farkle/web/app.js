const $ = (id) => document.getElementById(id);
const STORE = 'farkleInventory';
const PRESETS = 'farklePresets';

let dice = [];                 // every die the wiki knows about
let byName = {};
let fillDie = 'Ordinary die';  // what the game puts in unfilled slots
let owned = {};                // name -> how many you have
let peakFace = 1;              // highest single-face probability across all dice

// --- boot --------------------------------------------------------------------

fetch('/api/dice')
  .then((r) => r.json())
  .then((d) => {
    dice = d.dice;
    fillDie = d.fill_die;
    byName = Object.fromEntries(dice.map((x) => [x.name, x]));
    // Bars are scaled against the single heaviest face in the whole collection,
    // so a full bar means "as loaded as any die in the game gets".
    peakFace = Math.max(...dice.flatMap((d) =>
      d.weights ? d.weights.map((w) => w / d.total) : [0]));
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || '{}');
      for (const [n, c] of Object.entries(saved)) {
        if (byName[n] && !byName[n].wildcard && n !== fillDie) owned[n] = c;
      }
    } catch { /* corrupt storage is not worth crashing over */ }
    renderAll();
    renderPresetList();
  })
  .catch((e) => showError(`Could not load dice data: ${e.message}`));

const save = () => localStorage.setItem(STORE, JSON.stringify(owned));
const plural = (name) => name.toLowerCase().replace('die', 'dice');
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function renderAll() { renderList(); renderOwned(); }

// --- hover tooltip, in the spirit of the game's Item Info card ---------------

function showTip(name, target) {
  const d = byName[name];
  if (!d) return;
  const faces = d.weights
    ? d.weights.map((w, i) => {
        const p = w / d.total;
        const scoring = i === 0 || i === 4;   // only 1s and 5s score on their own
        return `
          <div class="face ${scoring ? 'is-scoring' : ''}">
            <span class="pip">${i + 1}</span>
            <span class="track"><span class="fill"
              style="width:${((p / peakFace) * 100).toFixed(1)}%"></span></span>
            <span class="pct">${(p * 100).toFixed(1)}%</span>
          </div>`;
      }).join('')
    : '<p class="muted">No published odds.</p>';

  $('tip').innerHTML = `
    <div class="bar">${esc(name)}</div>
    <div class="tip-body">
      ${d.desc ? `<p class="desc">${esc(d.desc)}</p>` : ''}
      ${d.wildcard ? `<p class="desc">${esc(d.wildcard)}</p>` : faces}
    </div>`;
  $('tip').style.display = 'block';
  positionTip(target);
}

function positionTip(target) {
  const tip = $('tip');
  const r = target.getBoundingClientRect();
  const h = tip.offsetHeight;
  let top = Math.max(8, Math.min(r.top + r.height / 2 - h / 2, window.innerHeight - h - 8));
  // Prefer the left of the row; flip to the right if there is no room.
  let left = r.left - tip.offsetWidth - 12;
  if (left < 8) left = Math.min(r.right + 12, window.innerWidth - tip.offsetWidth - 8);
  tip.style.top = `${top}px`;
  tip.style.left = `${left}px`;
}

const hideTip = () => { $('tip').style.display = 'none'; };

function wireTips(container) {
  container.addEventListener('mouseover', (e) => {
    const row = e.target.closest('[data-name]');
    if (row) showTip(row.dataset.name, row);
  });
  container.addEventListener('mouseout', (e) => {
    const row = e.target.closest('[data-name]');
    if (row && !row.contains(e.relatedTarget)) hideTip();
  });
  container.addEventListener('scroll', hideTip);
}

// --- dice rows ---------------------------------------------------------------

/* Links open the wiki page for the die. rel="noreferrer" because the target is
   third-party, and stopPropagation so following a link never also adds a die. */
function wikiLink(d) {
  return d.wiki
    ? ` <a class="wiki" href="${esc(d.wiki)}" target="_blank" rel="noopener noreferrer"
           title="Read about ${esc(d.name)} on the wiki">wiki</a>`
    : '';
}

function dieRow(d, count) {
  if (d.wildcard) {
    return `
      <div class="die is-blocked" data-name="${esc(d.name)}">
        <img src="img/${d.icon}" alt="">
        <div>
          <div class="die-name">${esc(d.name)}${wikiLink(d)}</div>
          <div class="die-sub">wildcard die &mdash; not supported</div>
        </div>
        <div class="muted">&mdash;</div>
      </div>`;
  }
  return `
    <div class="die ${count ? 'is-chosen' : ''}" data-name="${esc(d.name)}">
      <img src="img/${d.icon}" alt="">
      <div>
        <div class="die-name">${esc(d.name)}${wikiLink(d)}</div>
        <div class="die-sub">${esc(d.desc || '')}</div>
      </div>
      <div class="qty">
        <button data-act="dec" data-die="${esc(d.name)}" ${count ? '' : 'disabled'}>&minus;</button>
        <span>${count}</span>
        <button data-act="inc" data-die="${esc(d.name)}" ${count >= 6 ? 'disabled' : ''}>+</button>
      </div>
    </div>`;
}

/* The chest answers a different question from the catalogue: not "what does
   this die do?" but "what have I got?". So it drops the descriptions and shows
   tiles with a count badge, the way the game's inventory does. */
function ownedTile(d, count) {
  return `
    <div class="tile" data-name="${esc(d.name)}">
      <img src="img/${d.icon}" alt="">
      ${count > 1 ? `<span class="badge">${count}</span>` : ''}
      <span class="tile-name">${esc(d.name)}</span>
      <div class="tile-qty">
        <button data-act="dec" data-die="${esc(d.name)}">&minus;</button>
        <button data-act="inc" data-die="${esc(d.name)}"
                ${count >= 6 ? 'disabled' : ''}>+</button>
      </div>
    </div>`;
}

function renderList() {
  const q = $('search').value.toLowerCase().trim();
  // The fill die is supplied free in any unfilled slot, so it is always implied --
  // never something to add to the chest. Keep it out of the catalogue entirely.
  const hits = dice.filter((d) => d.name !== fillDie
                                  && (!q || d.name.toLowerCase().includes(q)));
  $('dice-list').innerHTML = hits.map((d) => dieRow(d, owned[d.name] || 0)).join('')
    || '<p class="muted center" style="padding:22px">No dice match that name.</p>';
}

function renderOwned() {
  const entries = Object.entries(owned).filter(([, c]) => c > 0)
    .sort((a, b) => a[0].localeCompare(b[0]));
  const total = entries.reduce((a, [, c]) => a + c, 0);

  // Say what the chest means for the search, not just how full it is.
  const short = Math.max(0, 6 - total);
  $('tally').innerHTML = total === 0
    ? `<b>Empty</b> &mdash; six ${plural(fillDie)} will be tried`
    : `<b>${total}</b> ${total === 1 ? 'die' : 'dice'}, `
      + `${entries.length} ${entries.length === 1 ? 'kind' : 'kinds'} &mdash; `
      + (short
        ? `${short} ${short === 1 ? 'slot' : 'slots'} filled with ${plural(fillDie)}`
        : `six go on the table`);

  $('owned-list').innerHTML = entries.length
    ? `<div class="tiles">${entries.map(([n, c]) => ownedTile(byName[n], c)).join('')}</div>`
    : `<p class="muted center" style="padding:22px">
         Add dice from the catalogue on the left.</p>`;
}

function bump(name, delta) {
  const cur = owned[name] || 0;
  const next = Math.max(0, Math.min(6, cur + delta));
  if (next) owned[name] = next; else delete owned[name];
  save();
  renderAll();
}

for (const id of ['dice-list', 'owned-list']) {
  $(id).addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (btn) bump(btn.dataset.die, btn.dataset.act === 'inc' ? 1 : -1);
  });
  wireTips($(id));
}
wireTips($('result-list'));   // the chips in each result carry data-name too

$('search').addEventListener('input', renderList);
$('clear').addEventListener('click', () => { owned = {}; save(); renderAll(); });

// --- saved sets --------------------------------------------------------------

const readPresets = () => {
  try { return JSON.parse(localStorage.getItem(PRESETS) || '{}'); } catch { return {}; }
};

function renderPresetList() {
  const sel = $('preset-pick');
  const keep = sel.value;
  sel.innerHTML = '<option value="">Load a set...</option>';
  for (const name of Object.keys(readPresets()).sort()) {
    const o = document.createElement('option');
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  }
  sel.value = keep;
}

$('preset-save').addEventListener('click', () => {
  if (!Object.values(owned).reduce((a, c) => a + c, 0)) return showError('Nothing to save yet.');
  const name = prompt('Name this set:');
  if (!name || !name.trim()) return;
  const presets = readPresets();
  presets[name.trim()] = owned;
  localStorage.setItem(PRESETS, JSON.stringify(presets));
  renderPresetList();
  $('preset-pick').value = name.trim();
});

$('preset-load').addEventListener('click', () => {
  const saved = readPresets()[$('preset-pick').value];
  if (!saved) return;
  owned = {};
  for (const [n, c] of Object.entries(saved)) {
    if (byName[n] && !byName[n].wildcard && n !== fillDie) owned[n] = c;
  }
  save();
  renderAll();
});

$('preset-del').addEventListener('click', () => {
  const name = $('preset-pick').value;
  if (!name) return;
  const presets = readPresets();
  delete presets[name];
  localStorage.setItem(PRESETS, JSON.stringify(presets));
  renderPresetList();
});

// --- solving -----------------------------------------------------------------

const STAGE = {
  queued: 'Preparing...',
  ranking: 'Weighing every possible set...',
  filtering: 'Sifting the strongest sets...',
  // No count yet: the first sets are still in flight and nothing has landed.
  starting: 'Solving the turn exactly...',
  solving: 'Solving the turn exactly...',
};

/* One continuous bar across the three stages, so it never resets to zero midway.
   Ranking is nearly free; the turn_ev sieve is a modest slice; the exact
   need-aware solve is most of the wait. */
const RANK_END = 0.06, FILTER_END = 0.28;

function progressFraction(job) {
  const part = job.total ? job.done / job.total : 0;
  if (job.stage === 'ranking') return part * RANK_END;
  if (job.stage === 'filtering') return RANK_END + part * (FILTER_END - RANK_END);
  if (job.stage === 'starting') return FILTER_END;
  if (job.stage === 'solving') return FILTER_END + part * (1 - FILTER_END);
  return 0;
}

function progressText(job) {
  const label = STAGE[job.stage] || 'Working...';
  return (job.stage === 'solving' || job.stage === 'filtering') && job.total
    ? `${label} ${job.done} of ${job.total}`
    : label;
}

/* The button itself is the progress bar: clicking dims it, and as the solve
   advances its original colour recovers left-to-right, so it is a whole button
   again when done. The fill element masks the *unfilled* (right) part, so its
   width is the remaining fraction. One control, no layout shift. */
function setGo(fraction, text) {
  $('go-fill').style.width = `${((1 - fraction) * 100).toFixed(1)}%`;
  $('go-label').textContent = text;
}

function resetGo() {
  $('go').disabled = false;
  $('go').classList.remove('busy');
  $('go-fill').style.width = '0%';
  $('go-label').textContent = 'Find the Best Set';
}

function showError(msg) {
  $('error').textContent = msg;
  $('error').style.display = '';
  resetGo();
}

async function run() {
  $('error').style.display = 'none';
  $('results').style.display = 'none';
  $('go').disabled = true;
  $('go').classList.add('busy');
  setGo(0, STAGE.queued);

  try {
    const res = await fetch('/api/optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ inventory: owned, refine: Number($('depth').value),
                             target: Number($('target').value) }),
    });
    const started = await res.json();
    if (!res.ok) throw new Error(started.error || 'request failed');
    poll(started.job);
  } catch (err) {
    showError(err.message);
  }
}

$('go').addEventListener('click', run);

function poll(jobId) {
  const tick = async () => {
    let job;
    try {
      job = await (await fetch(`/api/job?id=${jobId}`)).json();
    } catch (err) {
      return showError(`Lost contact with the server: ${err.message}`);
    }
    if (job.stage === 'error') return showError(job.error);
    if (job.stage === 'done') {
      resetGo();
      return render(job.result);
    }
    setGo(progressFraction(job), progressText(job));
    setTimeout(tick, 150);
  };
  tick();
}

// --- results -----------------------------------------------------------------

const pct = (x) => (x == null ? '&mdash;' : `${Math.round(x * 100)}%`);

/* The card stat row, all under the NEED-AWARE policy at the selected game-points
   target (turns to win stays the headline in the summary, so it isn't repeated
   here): three tiles on one level -- the chance a whole turn banks everything still
   needed, and the two-sided bust picture. The optimal play gambles hard while far
   from the target and banks the instant it can, so the bust rate is high early and
   collapses near the finish. Re-read on every target change, because paintResults()
   re-renders the list when the dropdown moves. */
function cardStats(r) {
  const w = r.win1 ? r.win1[target] : null;
  const bf = r.bust_far ? r.bust_far[target] : null;
  const be = r.bust_end;
  return `
    <div class="cardstats" title="All at your game-points target, under optimal need-aware play. Win in one turn: the chance a single turn banks the whole target. Bust rates: how often a turn ends in a bust when far from the target versus when under 600 points remain and the play banks as soon as it can.">
      <div class="cs-tile">
        <span class="cs-num">${pct(w)}</span>
        <span class="cs-lab">win in one turn</span>
      </div>
      <div class="cs-tile">
        <span class="cs-num ${bf >= 0.3 ? 'risky' : ''}">${pct(bf)}</span>
        <span class="cs-lab">bust rate, far off</span>
      </div>
      <div class="cs-tile">
        <span class="cs-num">${pct(be)}</span>
        <span class="cs-lab">bust rate, near the win</span>
      </div>
    </div>`;
}

/* The strategy, need-aware at the selected target. For a strong set the policy is a
   single sentence -- play for the whole thing -- so that is all we show. A weaker
   set banks early in a few (hand size, need) spots; those become "exception" rows.
   Either way, the per-hand throw risk follows as its own small table. */
function strategyPanel(r) {
  const strat = r.strategy || { simple: true, exceptions: [] };
  let body;
  if (strat.simple) {
    body = `<p class="strat-line">Play for the whole thing: keep throwing until your
      tally covers what you still need, then pass. This holds at any distance from
      the target.</p>`;
  } else {
    // Collapse repeats: the same (hand size, pass-at) across several needs is one
    // row, tagged with the smallest need it starts applying from.
    const groups = new Map();
    for (const e of strat.exceptions) {
      const k = `${e.n}|${e.bank}`;
      const g = groups.get(k);
      if (!g || e.need < g.need) groups.set(k, { ...e });
    }
    const rows = [...groups.values()]
      .sort((a, b) => b.n - a.n || a.need - b.need)
      .map((e) => `<li>${e.n} ${e.n === 1 ? 'die' : 'dice'}, needing
        ${e.need.toLocaleString()}+: pass at
        <span class="num">${e.bank.toLocaleString()}</span></li>`).join('');
    body = `<p class="strat-line">Mostly play for the whole thing, but bank early in
      a few spots where the dice in hand are too few to justify pushing on:</p>
      <p class="strat-head">Exceptions</p>
      <ul class="strat-exc">${rows}</ul>`;
  }
  return body + riskTable(r);
}

/* "Risk of throwing": the chance a throw of N dice scores nothing, under the
   need-aware policy at the selected target -- the genuinely useful residue of the
   old policy tabs. */
function riskTable(r) {
  const risk = (r.roll_risk && r.roll_risk[target]) || {};
  let rows = '';
  for (let n = 6; n >= 1; n--) {
    const v = risk[n];
    rows += `<tr><th>${n} ${n === 1 ? 'die' : 'dice'}</th>
      <td class="${v >= 0.5 ? 'risky' : ''}">${pct(v)}</td></tr>`;
  }
  return `<p class="strat-head">Risk of throwing</p>
    <table class="risk">${rows}</table>`;
}

let lastResults = [];   // last solve's results, re-sorted client-side on target change
let target = '4000';    // the game-points target the sets are ranked for, matching #target

/* Fewest need-aware turns to the chosen target wins, so g[target] is the sort key.
   The keys arrive as strings ("4000") because JSON has no integer keys -- `target`
   is kept as a string to match. Re-sorting is client-side: g at every standard
   target is in the payload, so changing it only reorders, never re-solves. */
function sortResults() {
  lastResults.sort((a, b) => a.g[target] - b.g[target]);
}

function render(data) {
  lastResults = data.results;
  $('results-note').textContent =
    `Weighed ${data.combinations_ranked.toLocaleString()} sets, sifted the top `
    + `${(data.filtered || 0).toLocaleString()} by turn value, then solved the best `
    + `${data.solved || data.results.length} exactly in ${data.solve_seconds}s.`;
  sortResults();
  paintResults();
  $('results').style.display = '';
}

function paintResults() {
  if (!lastResults.length) {
    $('result-list').innerHTML = '<p class="notice">No valid sets found.</p>';
    return;
  }
  $('result-list').innerHTML = lastResults.map((r, i) => {
      const chips = r.counts.map(([name, c]) => `
        <span class="chip" data-name="${esc(name)}">
          <img src="img/${byName[name] ? byName[name].icon : ''}" alt="">
          ${esc(name)}${c > 1 ? ` <span class="x">&times;${c}</span>` : ''}
        </span>`).join('');
      const usesFill = r.counts.some(([n]) => n === fillDie);

      return `
        <details class="result ${i === 0 ? 'is-best' : ''}">
          <summary>
            <div class="rowline">
              <div class="rank">${i + 1}</div>
              <div class="setline">${chips}</div>
              <div class="score">
                <div class="n">${r.g[target].toFixed(2)}</div>
                <div class="unit">turns to win</div>
              </div>
            </div>
          </summary>
          <div class="strategy">
            ${cardStats(r)}
            <details class="more">
              <summary class="more-sum">Strategy &mdash; when to throw, when to pass</summary>
              ${strategyPanel(r)}
              <p class="caption">Your score is the round tally plus the dice you set
                aside; all figures are for the need-aware play at the selected
                game-points target.${usesFill ? ` Includes ${plural(fillDie)} (supplied free).` : ''}</p>
            </details>
          </div>
        </details>`;
    }).join('');
}

/* The need curve is now solved only as far as the target (points past it never
   help), so a higher target than the last solve isn't in the payload to re-rank
   by -- re-solve for it (cheap for lower targets, and repeats hit the disk cache).
   A target already covered just reorders client-side, instantly. */
$('target').addEventListener('change', () => {
  target = $('target').value;
  if (!lastResults.length) return;
  if (lastResults.every((r) => r.g[target] != null)) {
    sortResults();
    paintResults();
  } else {
    run();
  }
});


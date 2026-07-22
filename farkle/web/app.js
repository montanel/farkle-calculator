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
        if (byName[n] && !byName[n].wildcard) owned[n] = c;
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
  const hits = dice.filter((d) => !q || d.name.toLowerCase().includes(q));
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
    if (byName[n] && !byName[n].wildcard) owned[n] = c;
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
  // No count yet: the first sets are still in flight and nothing has landed.
  starting: 'Solving the turn exactly...',
  solving: 'Solving the turn exactly...',
};

/* One continuous bar across both stages, so it never resets to zero midway.
   Ranking is a small fraction of the work; the exact solve is nearly all of it. */
const RANK_SHARE = 0.12;

function progressFraction(job) {
  const part = job.total ? job.done / job.total : 0;
  if (job.stage === 'ranking') return part * RANK_SHARE;
  if (job.stage === 'starting') return RANK_SHARE;
  if (job.stage === 'solving') return RANK_SHARE + part * (1 - RANK_SHARE);
  return 0;
}

function progressText(job) {
  const label = STAGE[job.stage] || 'Working...';
  return job.stage === 'solving' && job.total
    ? `${label} ${job.done} of ${job.total}`
    : label;
}

function showError(msg) {
  $('error').textContent = msg;
  $('error').style.display = '';
  $('progress').style.display = 'none';
}

$('go').addEventListener('click', async () => {
  $('error').style.display = 'none';
  $('results').style.display = 'none';
  $('progress').style.display = '';
  $('progress-fill').style.width = '0%';
  $('progress-label').textContent = STAGE.queued;
  $('go').disabled = true;

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
    $('go').disabled = false;
    showError(err.message);
  }
});

function poll(jobId) {
  const tick = async () => {
    let job;
    try {
      job = await (await fetch(`/api/job?id=${jobId}`)).json();
    } catch (err) {
      $('go').disabled = false;
      return showError(`Lost contact with the server: ${err.message}`);
    }
    if (job.stage === 'error') {
      $('go').disabled = false;
      return showError(job.error);
    }
    if (job.stage === 'done') {
      $('go').disabled = false;
      $('progress').style.display = 'none';
      return render(job.result);
    }
    $('progress-label').textContent = progressText(job);
    $('progress-fill').style.width = `${(progressFraction(job) * 100).toFixed(1)}%`;
    setTimeout(tick, 150);
  };
  tick();
}

// --- results -----------------------------------------------------------------

const ROLL_ON = '<span class="always">always throw</span>';

/* One safe number per row. `safe` is the lowest threshold across every hand of
   that size, so acting on it never rolls past the right moment for the hand you
   actually hold -- the exact number for your hand is in the breakdown below.

   The two number columns answer different questions and are easy to confuse.
   "Rolling busts" is the risk you take by rolling that hand -- the one that
   feels relevant at the table. "Turns end here" is where turns finish, which is
   dominated by *banking*, so the hand you most often finish on is the one you
   most often stop on, not the one that kills you. */
/* Quoted in the caption, so it has to be the real figure for these dice: a set
   that scores easily can drop the last-die risk well under half. */
function lastDieRisk(shape) {
  const r = shape.by_size[1].bust_if_roll;
  return r === null ? 'almost none' : `${(r * 100).toFixed(0)}%`;
}

/* One row per count of thrown dice, expandable in place: the headline number is
   the safe one (lowest across every hand of that size), and clicking the row
   unfolds which specific dice carry which exact number. */
function strategyRows(bankAt, shape) {
  const bySize = bankAt.by_size;
  const hands = {};
  for (const h of bankAt.hands) (hands[h.n] = hands[h.n] || []).push(h);

  let out = `
    <tr class="colhead">
      <th>you'd throw</th><th>what to do</th>
      <th class="freq">the throw busts</th>
    </tr>`;
  for (let n = 6; n >= 1; n--) {
    const s = bySize[n] || {};
    const roll = s.safe === null || s.safe === undefined;
    const ends = shape ? shape[n] : null;
    const risk = ends && ends.bust_if_roll !== null ? ends.bust_if_roll * 100 : null;
    const hs = hands[n] || [];
    out += `
      <tr class="sizerow" data-n="${n}"
          title="Show the exact number for each hand of ${n}">
        <th><span class="caret">&#9656;</span>${n} ${n === 1 ? 'die' : 'dice'}</th>
        <td>${roll ? ROLL_ON
          : `throw below <span class="num">${s.safe.toLocaleString()}</span> &mdash; pass at or above`}</td>
        <td class="freq">${risk === null ? ''
          : `<span class="${risk >= 50 ? 'risky' : ''}">${risk.toFixed(0)}%</span>`}</td>
      </tr>`;
    out += hs.map((h) => `
      <tr class="subrow sub-${n}">
        <td class="handcell">${handName(h.dice)}</td>
        <td>${h.bank === null ? ROLL_ON
          : `throw below <span class="num">${h.bank.toLocaleString()}</span> &mdash; pass at or above`}</td>
        <td class="freq"></td>
      </tr>`).join('');
  }
  return out;
}

/* The shape behind the single "points / turn" mean. `turn_ev` is that mean, and
   for aggressive sets it is unrepresentative: a big slice of turns bust (bank 0)
   while a rare hot-dice tail drags the average far above a typical turn. Showing
   the median, the middle-half range, the bust rate and the mean-when-it-banks
   makes that skew visible instead of trusting one flattering number. */
function turnDistBlock(td, turnEv) {
  if (!td) return '';
  const n = (x) => Math.round(x).toLocaleString();
  const pct = (x) => `${(x * 100).toFixed(0)}%`;
  const cond = td.cond_mean === null || td.cond_mean === undefined
    ? '&mdash;' : n(td.cond_mean);
  return `
    <div class="turndist">
      <div class="td-grid">
        <div class="td-stat">
          <span class="td-num">${n(td.median)}</span>
          <span class="td-lab">median turn</span></div>
        <div class="td-stat">
          <span class="td-num">${n(td.p25)}&ndash;${n(td.p75)}</span>
          <span class="td-lab">middle half</span></div>
        <div class="td-stat">
          <span class="td-num ${td.bust >= 0.3 ? 'risky' : ''}">${pct(td.bust)}</span>
          <span class="td-lab">turns bust</span></div>
        <div class="td-stat">
          <span class="td-num">${cond}</span>
          <span class="td-lab">avg when it banks</span></div>
      </div>
      <p class="td-note">Mean <b>${n(turnEv)}</b> pts is the average under
        <em>unlimited points-max play</em> &mdash; pulled up by a rare huge tail,
        not a score to expect. The <b>median</b> is what a typical turn banks.</p>
    </div>`;
}

/* "Aranka's die, Aranka's die, Fer die" -> "Aranka's die x2, Fer die". */
function handName(dice) {
  const seen = new Map();
  for (const d of dice) seen.set(d, (seen.get(d) || 0) + 1);
  return [...seen].map(([name, c]) =>
    esc(name) + (c > 1 ? ` <span class="x">&times;${c}</span>` : '')).join(', ');
}

let lastResults = [];   // the advisor needs the full dice list of a chosen set
let target = '4000';    // the wager the sets are ranked for, matching #target

/* Fewest need-aware turns to the chosen wager wins, so g[target] is the sort key.
   The keys arrive as strings ("4000") because JSON has no integer keys -- `target`
   is kept as a string to match. Re-sorting is client-side: g at every standard
   wager is in the payload, so changing the wager only reorders, never re-solves. */
function sortResults() {
  lastResults.sort((a, b) => a.g[target] - b.g[target]);
}

function render(data) {
  lastResults = data.results;
  $('results-note').textContent =
    `Weighed ${data.combinations_ranked.toLocaleString()} sets in ${data.rank_seconds}s, `
    + `then solved the top ${data.results.length} exactly in ${data.solve_seconds}s.`;
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
                <div class="n">${r.g[target].toFixed(1)}</div>
                <div class="unit">turns to win</div>
                <div class="sub">${Math.round(r.turn_ev).toLocaleString()} pts / turn</div>
              </div>
            </div>
          </summary>
          <div class="strategy">
            ${turnDistBlock(r.turn_dist, r.turn_ev)}
            <p class="playline">
              <button class="btn-sm play" data-i="${i}">&#9656; Play along &mdash;
                enter each throw, be told what to keep</button>
            </p>
            <p class="caption">
              Read this at the <b>throw or pass</b> choice &mdash; after you have
              picked out the dice to set aside, before you press anything. Count
              the dice that would be thrown (the ones you did <em>not</em> select)
              and find that row. Your score is this round's tally <em>plus</em>
              the selection in front of you &mdash; the middle and bottom numbers
              at the edge of the table, added together. Click a row to see the
              exact number for each combination of dice.
              ${usesFill ? `Includes ${plural(fillDie)}, which the game supplies free.` : ''}
            </p>
            <table class="rows">${strategyRows(r.bank_at, r.shape.by_size)}</table>
            <p class="caption">
              <b>The throw busts</b> is the chance that throw scores nothing. A
              high figure does not make throwing wrong: the threshold already
              weighs it. Your last die loses everything
              <span class="num">${lastDieRisk(r.shape)}</span> of the time, yet
              throwing it is the only way to refill the table with all six, and
              that payoff covers the losses. Whole turns end in a bust
              <span class="num">${(r.shape.bust * 100).toFixed(0)}%</span> of the
              time. A row with no number means the maths never finds a reason to
              pass there.
            </p>
          </div>
        </details>`;
    }).join('');
}

$('target').addEventListener('change', () => {
  target = $('target').value;
  if (lastResults.length) { sortResults(); paintResults(); }
});

// --- play-along advisor ------------------------------------------------------
// Mirrors one turn of the real game. The page tracks which physical dice are
// still on the table (a bitmask) and the round tally; each throw, the player
// enters the faces and the server answers with the DP's own move: what to set
// aside, then throw or pass.

let adv = null;   // { names[6], g, target, banked, mask, t, faces{}, last }

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/* The advice is conditioned on the remaining need, so the advisor tracks the
   wager and the player's banked game total (need = wager - banked) on top of
   the within-turn tally. The set's need curve (r.need_g) rides along so the
   server never re-solves it. */
function openAdvisor(r) {
  const tgt = Number(target) || 4000;   // open at the wager the results are ranked for
  adv = { names: r.dice.slice(), g: r.need_g || null, target: tgt, banked: 0,
          mask: 63, t: 0, faces: {}, last: null };
  $('advisor').style.display = '';
  $('adv-set').innerHTML = r.counts.map(([name, c]) => `
    <span class="chip" data-name="${esc(name)}">
      <img src="img/${byName[name] ? byName[name].icon : ''}" alt="">
      ${esc(name)}${c > 1 ? ` <span class="x">&times;${c}</span>` : ''}
    </span>`).join('');
  $('adv-target').value = String(tgt);
  $('adv-banked').max = tgt;
  $('adv-banked').value = 0;
  $('adv-t').value = 0;
  renderAdvNeed();
  renderAdvEntry();
  // Build this (set, wager)'s tables now, while the player enters faces; shipping
  // the need curve lets the server skip the dear solve.
  warmAdvisor();
  $('advisor').scrollIntoView({ behavior: 'smooth' });
}

const advNeed = () => Math.max(0, adv.target - adv.banked);

function renderAdvNeed() {
  const need = advNeed();
  $('adv-need').innerHTML = need > 0
    ? `need <b>${need.toLocaleString()}</b> to win`
    : `<b>already won</b> &mdash; you hold ${adv.banked.toLocaleString()}`;
}

function warmAdvisor() {
  fetch('/api/advise', { method: 'POST', headers: JSON_HEADERS,
    body: JSON.stringify({ dice: adv.names, warm: true,
                           target: adv.target, g: adv.g }) }).catch(() => {});
}

function syncBanked() {
  $('adv-banked').value = adv.banked;
  renderAdvNeed();
}

function renderAdvEntry() {
  const n = adv.mask.toString(2).split('1').length - 1;
  const rows = [];
  for (let i = 0; i < 6; i++) {
    if (!(adv.mask >> i & 1)) continue;
    const d = byName[adv.names[i]];
    rows.push(`
      <div class="adie">
        <img src="img/${d.icon}" alt="">
        <span class="adie-name" data-name="${esc(d.name)}">${esc(d.name)}</span>
        <span class="faces">${[1, 2, 3, 4, 5, 6].map((f) =>
          `<button data-i="${i}" data-face="${f}"
                   class="${adv.faces[i] === f ? 'sel' : ''}">${f}</button>`).join('')}</span>
      </div>`);
  }
  $('adv-dice').innerHTML = `
    <p class="adv-sub">You threw ${n} ${n === 1 ? 'die' : 'dice'} &mdash;
       what did ${n === 1 ? 'it' : 'each'} show?
       <span class="keyhint">type ${n === 1 ? 'the face' : 'the faces in order'}
       &middot; Enter asks</span></p>` + rows.join('');
  $('adv-out').innerHTML = '';
  adv.last = null;
  updateAdvGo();
}

function updateAdvGo() {
  let missing = false;
  for (let i = 0; i < 6; i++) {
    if (adv.mask >> i & 1 && !adv.faces[i]) missing = true;
  }
  $('adv-go').disabled = missing;
}

async function doAdvise() {
  const faces = {};
  for (let i = 0; i < 6; i++) if (adv.mask >> i & 1) faces[i] = adv.faces[i];
  $('adv-go').disabled = true;
  try {
    const res = await fetch('/api/advise', { method: 'POST', headers: JSON_HEADERS,
      body: JSON.stringify({ dice: adv.names, mask: adv.mask, t: adv.t,
                             banked: adv.banked, target: adv.target, g: adv.g, faces }) });
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || `request failed (${res.status})`);
    adv.last = out;
    renderAdvice(out);
  } catch (err) {
    $('adv-out').innerHTML = `<p class="notice">${esc(err.message)}</p>`;
  }
  updateAdvGo();
}

/* The advice is now in turns-to-win, not points: banking the instant the tally
   covers the need wins outright, and otherwise the choice is whichever costs
   fewer expected turns, with the chance of clearing the need this very turn
   shown alongside. */
function renderAdvice(o) {
  if (o.won_already) {
    $('adv-out').innerHTML = `
      <div class="advice">
        <p class="adv-action">You already hold the wager &mdash; the game is won.</p>
        <p class="adv-ev">Lower "your score so far" to plan an earlier turn.</p>
      </div>`;
    return;
  }
  if (o.bust) {
    $('adv-out').innerHTML = `
      <div class="advice advice--bad">
        <p class="adv-action">Farkle &mdash; nothing scores.</p>
        <p class="adv-ev">${o.lost
          ? `The ${o.lost.toLocaleString()} you had set aside this round is lost.`
          : 'Nothing was lost but the throw.'}</p>
        <button id="adv-next" class="btn-sm">Start the next turn</button>
      </div>`;
    return;
  }
  const keeps = o.keep.map((i) => {
    const d = byName[adv.names[i]];
    return `<span class="chip" data-name="${esc(d.name)}">
      <img src="img/${d.icon}" alt=""> ${esc(d.name)}
      <span class="rolled">${adv.faces[i]}</span></span>`;
  }).join('');

  const remaining = Math.max(0, o.need - o.turn_score);
  const winPct = `${(o.clear_prob * 100).toFixed(0)}%`;
  const rollVs = `Throwing on wins in about <b>${o.roll_turns}</b> more turns
    vs ${o.bank_turns} for banking now &mdash; and a <b>${winPct}</b> chance you
    clear the ${o.need.toLocaleString()} this very turn.`;

  let actionLine, evLine, nextLabel;
  if (o.won) {
    actionLine = `Then PASS &mdash; banking ${o.turn_score.toLocaleString()}
                  reaches the wager. <b>You win.</b>`;
    evLine = `${o.turn_score.toLocaleString()} clears the
              ${o.need.toLocaleString()} you needed.`;
    nextLabel = 'Won &mdash; start a new game';
  } else if (o.action === 'bank') {
    actionLine = `Then PASS &mdash; bank the ${o.turn_score.toLocaleString()}.`;
    evLine = `Banking wins in about <b>${o.bank_turns}</b> more turns on average,
              fewer than the ${o.roll_turns} throwing on would cost. Still need
              ${remaining.toLocaleString()} after this.`;
    nextLabel = `Banked ${o.turn_score.toLocaleString()} &mdash; next turn`;
  } else if (o.hot) {
    actionLine = 'Then THROW AGAIN &mdash; hot dice, all six come back.';
    evLine = rollVs;
    nextLabel = 'I threw all six &mdash; enter the new roll';
  } else {
    actionLine = `Then THROW the other ${o.reroll_count}
                  ${o.reroll_count === 1 ? 'die' : 'dice'}.`;
    evLine = rollVs;
    nextLabel = `I threw the ${o.reroll_count} &mdash; enter the new roll`;
  }

  $('adv-out').innerHTML = `
    <div class="advice${o.won ? ' advice--win' : ''}">
      <p class="adv-sub">Set aside these &mdash; worth ${o.points.toLocaleString()},
         round tally ${o.turn_score.toLocaleString()}:</p>
      <p class="adv-keep">${keeps}</p>
      <p class="adv-action">${actionLine}</p>
      <p class="adv-ev">${evLine}</p>
      <button id="adv-next" class="btn-sm">${nextLabel}</button>
    </div>`;
}

function resetAdvTurn() {
  adv.mask = 63;
  adv.t = 0;
  adv.faces = {};
  adv.last = null;
  $('adv-t').value = 0;
  renderAdvEntry();
}

$('result-list').addEventListener('click', (e) => {
  const p = e.target.closest('.play');
  if (p) return openAdvisor(lastResults[+p.dataset.i]);
  const row = e.target.closest('tr.sizerow');
  if (row) {
    row.classList.toggle('open');
    for (const sub of row.closest('table').querySelectorAll(`.sub-${row.dataset.n}`)) {
      sub.classList.toggle('show');
    }
  }
});

$('adv-dice').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-face]');
  if (!b) return;
  adv.faces[+b.dataset.i] = +b.dataset.face;
  $('adv-out').innerHTML = '';   // a changed roll invalidates old advice
  adv.last = null;
  for (const sib of b.parentElement.children) sib.classList.remove('sel');
  b.classList.add('sel');
  updateAdvGo();
});

$('adv-out').addEventListener('click', (e) => {
  if (e.target.id !== 'adv-next') return;
  const o = adv.last;
  if (!o) return resetAdvTurn();
  if (o.won) {                 // game over: reset the game score and start fresh
    adv.banked = 0;
    syncBanked();
    return resetAdvTurn();
  }
  if (o.bust) return resetAdvTurn();     // busted turn: game score unchanged
  if (o.action === 'bank') {             // banked a partial score: carry it into the game total
    adv.banked = Math.min(adv.target, adv.banked + o.turn_score);
    syncBanked();
    return resetAdvTurn();
  }
  adv.mask = o.next_mask;                // rerolling: continue the same turn
  adv.t = o.turn_score;
  adv.faces = {};
  $('adv-t').value = adv.t;
  renderAdvEntry();
});

// Changing the wager re-conditions everything; rebuild the server-side tables.
$('adv-target').addEventListener('change', () => {
  adv.target = Number($('adv-target').value);
  $('adv-banked').max = adv.target;
  if (adv.banked > adv.target) { adv.banked = adv.target; $('adv-banked').value = adv.banked; }
  renderAdvNeed();
  $('adv-out').innerHTML = '';
  adv.last = null;
  warmAdvisor();
});

// The banked game total sets the remaining need (need = wager - banked).
$('adv-banked').addEventListener('change', () => {
  const v = Math.max(0, Math.min(adv.target,
    Math.round((+$('adv-banked').value || 0) / 50) * 50));
  $('adv-banked').value = v;
  adv.banked = v;
  renderAdvNeed();
  $('adv-out').innerHTML = '';
  adv.last = null;
});

// The tally is editable so the tool can pick up a turn mid-way, or follow a
// player who kept different dice than advised.
$('adv-t').addEventListener('change', () => {
  const v = Math.max(0, Math.min(10000, Math.round((+$('adv-t').value || 0) / 50) * 50));
  $('adv-t').value = v;
  adv.t = v;
  $('adv-out').innerHTML = '';
  adv.last = null;
});

$('adv-go').addEventListener('click', doAdvise);
$('adv-reset').addEventListener('click', resetAdvTurn);
wireTips($('advisor'));

// Faces can be typed: digits fill the dice top to bottom, Backspace undoes the
// last one, Enter asks (or advances to the next throw once advice is shown).
// Much faster than 36 buttons when the game is running on the other monitor.
document.addEventListener('keydown', (e) => {
  if (!adv || $('advisor').style.display === 'none') return;
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
  if (e.key >= '1' && e.key <= '6') {
    for (let i = 0; i < 6; i++) {
      if (adv.mask >> i & 1 && !adv.faces[i]) { adv.faces[i] = +e.key; break; }
    }
    renderAdvEntry();
  } else if (e.key === 'Backspace') {
    let last = -1;
    for (let i = 0; i < 6; i++) if (adv.mask >> i & 1 && adv.faces[i]) last = i;
    if (last >= 0) { delete adv.faces[last]; renderAdvEntry(); }
    e.preventDefault();
  } else if (e.key === 'Enter') {
    e.preventDefault();   // a focused face button must not also re-trigger
    if (!$('adv-go').disabled) doAdvise();
    else if ($('adv-next')) $('adv-next').click();
  }
});

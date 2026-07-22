# KCD2 Farkle Optimiser (exact)

Finds the best 6-dice set from your inventory for the Farkle mini-game in
*Kingdom Come: Deliverance 2*, by computing the **exact expected score of a
whole turn under optimal play** — rerolls, which dice to keep, hot dice and
busting all included. There is no Monte Carlo anywhere; every number is
computed, not sampled.

```
python run.py
```

Stdlib only — no `pip install`. Opens <http://127.0.0.1:8000/>.

```
python tests.py            # rules + solver checks, ~15s
python tests.py --slow     # also Monte-Carlo validates the DP, ~2min
python check_filter.py     # brute-forces small inventories to audit the shortlist
```

## Why this exists

The original JS tool reported a single number per dice set: the average score of
**one roll of six dice**, estimated with 1,000 random trials. That misses the
part of Farkle that actually matters — deciding whether to bank or reroll — and
it estimates by sampling a quantity that can be computed exactly.

For six fair dice the difference is large:

| | |
|---|---|
| Best score from one roll, then stop | **399** |
| Expected points per turn, playing optimally | **564** |

Optimal play is worth **41% more** than the greedy one-roll figure, and the two
rank dice sets differently. In a 15-die test inventory the eight best sets all
had first-roll values within 2% of each other (640–654) while their true turn
values spanned 1380–1622 — and the set with the *highest* first-roll value
placed only 5th.

## How it works

### 1. Scoring (`farkle/rules.py`)

Rules are taken from the KCD2 wiki: singles (1 = 100, 5 = 50), partial straights
(1–5 = 500, 2–6 = 750), full straight = 1500, three of a kind (1s = 1000, else
face × 100), and **each die beyond the third doubles** the set — four 4s = 800,
five 4s = 1600, six 1s = 8000.

`best_score(counts)` returns the most points obtainable by setting aside *every*
die in a multiset, or `None` if that multiset isn't a legal set-aside. It
recurses over decompositions because greedy is wrong: `[1,1,2,3,4,5]` is worth
600 (straight + spare 1), not 250 (three singles).

### 2. The move table

For every hand (which of the six dice are still live) and every way it can land,
the table lists all legal set-asides and what each is worth. Selections are
tracked by **die index, not face value** — keeping "the 1 on die 0" and "the 1 on
die 3" score the same but leave you rerolling different dice, and since KCD2 dice
have different distributions those are genuinely different moves.

The table depends only on the rules, never on which dice you own, so it is built
once (~2s, 489,192 moves) and reused for every candidate set.

### 3. The turn solver (`farkle/solver.py`)

The state is `(hand, t)` — which dice are live, and the points accumulated so far
this turn. The value function is

```
V(hand, t) = max( t                              # bank and end the turn
                , E[ best move available ] )     # reroll `hand`
```

where a roll offering no legal set-aside is a bust worth 0, and setting aside all
six dice refills the hand (hot dice) rather than ending the turn.

Every score is a multiple of 50 so `t` lives on a 50-point grid, capped at 10,000
(above which banking is forced — reaching that in one turn is vanishingly rare
for normal dice). **Every legal set-aside scores at least 50, so any reroll
strictly increases `t`.** That means sweeping `t` downwards visits every state
after the states it depends on: one backward pass gives the exact answer, with no
iteration to convergence and no approximation.

The answer for a dice set is `V(all six dice, t=0)`.

### 4. Making it fast

Pure Python, ~1 second per dice set:

- **Merging outcomes by move-set.** Many different rolls offer exactly the same
  choice — every roll whose only scoring die is the 5 on die #3 plays identically
  regardless of the other five faces. Since the value of a move-set depends only
  on the move-set, such outcomes merge and their probabilities add. This collapses
  ~108k outcome evaluations per sweep to ~11k, and the bucketing is rules-only so
  it too is computed once. **~6× faster.**
- **Flat value array** indexed `V[hand * ROW + t]`, with each move's
  `(next_hand, points)` pre-baked into a single integer offset, so the innermost
  operation is one list index.
- **Padding instead of bounds checks.** The array extends 8,000 points past the
  cap, pre-filled with "you bank `t`", so `t + points` can never overrun.

### 5. Shortlisting

Solving every set exactly is too slow for a large inventory (a 20-die inventory
has 38,760 of them). So the app runs two stages:

1. Rank **every** set by exact first-roll value. A DFS over sorted die indices
   shares the face-count distribution of common prefixes, and at depth 5 collapses
   it into a 6-entry vector, so each candidate costs six multiplies. ~0.7s for
   5,005 sets.
2. Solve the top *N* (default 20) with the full turn DP and re-rank by the
   need-aware metric — fewest expected turns to the chosen wager, `g[target]`
   (see below). `turn_ev` and the legacy renewal ride along as diagnostics.

**This shortlist is a heuristic, and it is the one approximation in the pipeline.**
`check_filter.py` audits it by brute-forcing the *exact* metric over every set for
small inventories and reporting where the true optimum — the set **minimising
`g[target]`** — sat in the cheap first-roll ordering. It checks a short wager
(1,500) and a long one (4,000) separately, since the proxy may correlate
differently with each. Across a hand-picked strong 9-die inventory (84 sets) and
three random 8-die inventories, at both targets, the g-optimum's **worst position
on first-roll value was #2** — every shortlist size, down to 4, found it. First-
roll value tracks the need-aware objective at least as well as it tracked
`turn_ev`.

The default shortlist is 20, which leaves comfortable margin, but it **is not a
guarantee**. To be certain for a specific inventory, run `check_filter.py` on it.
The web UI exposes the depth (5 / 10 / 20 / 40); 20 is the recommended default.

### Solving in parallel

Each set in the shortlist is solved independently, so `_solve_many` fans them out
over a `ProcessPoolExecutor`. Results are byte-identical to the serial path —
there is no sampling anywhere, just the same deterministic DP run on more cores.

Worker count is capped at 8 rather than the core count; past a handful of workers
the coordination overhead outweighs the extra parallelism on a job this size.
If the pool cannot start at all, the code falls back to a serial loop — a
restricted environment should get slower answers, not none.

Two things make the workers cheap, both aimed at dead time the user watches:

- **The move table is shipped, not rebuilt.** Windows spawns bare interpreters,
  so each worker would otherwise spend ~2s rebuilding it — all at once, at the
  start of the run. Pickling the finished table is 0.7 MB and **7 ms** to load,
  and `groups()` short-circuits when `_GROUPS` is already populated.
- **The pool outlives the request.** Spawning eight interpreters costs seconds,
  so the pool is created once and reused, and `warm_up()` primes it during
  server startup so even the first solve skips the wait.

Measured on a 16-core machine, a 20-set solve went 42.0s serial → 11.8s → 10.7s
(shipping the table) → **8.5s** (warm pool).

Progress is reported with `submit` + `as_completed`, not `map`: `map` yields in
submission order, so one slow set silently holds back the count for every set
that finished behind it. The UI runs a single bar across both stages (ranking is
12% of it) and shows an indeterminate crawl while the first sets are in flight,
since nothing can be counted until one lands.

## Correctness

- `tests.py` checks every row of the wiki scoring table, the cases where greedy
  decomposition fails, illegal set-asides, and the dice data.
- `tests.py --slow` extracts the solver's own policy, plays 200,000 random turns
  with it, and checks the result matches the DP's claim. All within 1.3 standard
  errors.
- Degenerate dice give checkable answers: six synthetic always-roll-a-3 dice give
  six 3s = 2400 every time, never bust, and the solver correctly rolls to the cap.
- Selections are checked to always total six dice, to respect quantity caps, and
  an all-ordinary hand is checked to reproduce the fair-dice value exactly.

### Dice data

Every die was re-fetched from the wiki (`Category:KCD2: Dice`, July 2026) and
diffed against the original JS table. Results:

- **43 dice on the wiki, 36 in the original.** Missing were Monk's die,
  Mother-of-pearl die, Painter's die R/G/B, Tengri's die and Balatro's die.
- **One real data error:** Saint Antiochus' die was listed as `[0,0,100,0,0,0]`
  — always a 3. It is actually `[3,1,6,1,1,3]/15`, i.e. 40% threes. The original
  wildly overrated it.
- Everything else agreed to within rounding.

`farkle/dice.py` stores **integer face weights**, not the rounded percentages the
wiki displays. All 42 dice with published odds recover exactly to a small integer
weighting — Aranka's die is `[6,1,6,1,6,1]/21`, the Ci die is `[3,3,3,3,3,8]/23` —
verified to within 0.08 percentage points. The published percentages are rounded
(many sum to 99.8% or 100.3%); the weights are exact.

### Wiki links

Every die links to its wiki page. Nine pages carry a `/KCD2` disambiguation
suffix (`Ci die/KCD2`, `Lucky die/KCD2`, …) and are listed explicitly; the rest
resolve from the die name. `quote()` reproduces the wiki's own encoding, so
`King's die` becomes `King%27s_die` — verified against
`action=query&prop=info&inprop=url` for all 43 pages, which is also how the
disambiguated titles were found rather than guessed. Note that HEAD requests to
the site return 403 (bot filtering), so link-checking against the live pages is
not a useful test; the tests assert construction instead.

A re-scrape of every description found **one** error (King's die), since fixed —
all 43 now match the wiki exactly.

### Two dice are deliberately excluded

**Devil's head die** and **Balatro's die** carry a joker mechanic this solver does
not model — the devil's head "completes any combination but never scores on its
own", and every face of Balatro's die is chosen by the player when it lands.
Scoring them as ordinary weighted dice would produce confidently wrong numbers,
so they appear in the UI greyed out and the API rejects them. Modelling jokers
properly means adding a seventh face to the move table and teaching `best_score`
to substitute — tractable, but not done.

### Ordinary dice are always in play

The game fills any slot you do not fill yourself with an ordinary die, so the
solver always has six of them available on top of whatever you own. Owning three
special dice really means choosing between them and three free ordinary dice, and
the optimiser considers those selections automatically. Selections are enumerated
as multisets with per-die quantity caps, so owning two of a die lets you field
both.

### Two views of the same policy

A bank threshold belongs to a *hand* — the specific dice still in front of you —
not to a dice count, so the six hands of size 3 generally have six different
thresholds. `turn_ev(..., want_policy=True, names=...)` returns both readings:

- `by_size[n]["safe"]` — the lowest threshold across every hand of that size.
  Acting on it never rolls past the right moment for the hand you actually hold,
  which makes it the one number you can follow mid-game without a lookup.
- `hands` — every distinct hand, named, with its exact threshold. Hands made of
  identical dice collapse to one entry, so six of the same die yields six rows
  rather than 63.

`None` means the threshold sits above the 10,000 cap: keep rolling whatever your
score. The UI shows `safe` as a six-row table and hides `hands` behind a
disclosure, grouped by hand size.

### How often a rule actually fires

A threshold is easy to misread as a prediction. "Three dice: bank at 7,400" does
not mean you will hold three dice at 7,400 — only that *if* you did, you should
stop. Reaching it needs several hot-dice cycles, so it may never come up, and a
rule you never meet is just "always roll" wearing a number.

`_turn_shape` settles it by pushing probability forward through the solved
policy: start a full hand at zero, follow the policy's own choice at every state,
and record where the mass ends up. Since any move raises `t`, one upward sweep
visits each state after all its inflow has arrived — the mirror image of the
backward pass. For six fair dice:

| dice in hand | 6 | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|---|
| threshold | roll on | 3400 | 1050 | 450 | 250 | 300 |
| turns ending there | 3.5% | 4.9% | 6.3% | 14.7% | 39.2% | 31.4% |

Five dice carries the second-highest threshold and is nearly the rarest place to
be. Turns end in a bust 19.2% of the time. Outcomes are asserted to sum to 1,
which also catches mass silently falling off the end of the padded tail.

The same pass yields `bust_if_roll`: given the policy rolls that many dice, how
often the roll scores nothing. It is a different question from the row above and
is easy to conflate with it:

| dice in hand | 6 | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|---|
| rolling busts | 3.1% | 7.7% | 15.7% | 27.8% | 44.4% | 66.7% |
| turns ending there | 3.5% | 4.9% | 6.3% | 14.7% | 39.2% | 31.4% |

One die is both the most dangerous thing to roll and a common place to *finish* —
because finishing there is overwhelmingly banking (31.33%), not busting (0.05%).
Rolling a lone fair die busts exactly 4/6 of the time and is still correct below
the threshold, since scoring on it triggers hot dice and returns the full hand.
The UI shows both columns side by side for this reason.

### The shape of a turn, not just its mean

`turn_ev` is the **mean** of the per-turn banked score under the points-max
policy, and for a strong set that single number lies about a typical turn. The
policy is aggressive — it keeps rolling while the expected roll beats the tally —
so the turn distribution is a big lump of busts at 0 plus a long tail of huge
hot-dice runs. The mean is exact but sits well above what you usually bank.

`turn_distribution` bucketises the same forward pass `_turn_shape` runs — by how
much a turn banks rather than by how many dice it ends holding — and reports the
shape: the bust probability (mass at 0), the 25th/50th/75th percentiles (busts
counted as a banked 0), and the **conditional mean given the turn banked at all**.
It is still exact; no sampling. For six Heavenly Kingdom dice:

| bust | 25th pct | median | 75th pct | mean (`turn_ev`) | mean when it banks |
|---|---|---|---|---|---|
| 36.9% | 0 | 1,200 | 2,100 | 1,424 | 2,258 |

Over a third of turns bank nothing, so the 25th percentile is a flat 0 and the
median turn (1,200) sits far below both the mean and the mean-when-it-banks. The
UI shows these next to `turn_ev` and labels the mean *"under unlimited points-max
play"* — a diagnostic, not a score to expect. (The mean equals `turn_ev` by
construction, which the tests pin.)

### Need-aware turns to a wager (`g[target]`)

`turn_ev` and its distribution describe *one turn*. What decides a wager is how
many *turns* a whole game takes — and that depends on the policy. Playing every
turn for maximum points is wrong once the finish line is close: past the target
points are worthless, so the right move is to bank the instant a pass would cross
the line. That is **not** the policy `turn_ev` optimises, so ranking by it (or by
a renewal over its banked distribution) is ranking by the wrong game.

`need_curve` fixes this exactly. For each remaining need `r` (a multiple of 50 up
to 4,000) it computes `g[r]` — the expected turns to clear `r` under the policy
that *minimises* that number, every decision conditioned on `r`. Within a turn it
is a second DP, the mirror of the points-max one: take the **minimum** expected
turns instead of the maximum expected points, force banking once the tally covers
the need, and value a bust at `1 + g[r]` (it ends the turn and you still need `r`).
That bust term is the only self-reference; since its coefficient is the turn's
bust probability (< 1), one backward sweep that also accumulates that coefficient
jumps straight to the fixed point `g[r] = (g'[r] − b·x)/(1 − b)`, and a warm-started
handful of sweeps per `r` converges to 1e-9. No Monte Carlo, no iteration blow-up.

`g[target]` is the number the app **ranks by** and the headline "turns to win". It
is always ≤ the legacy renewal over the points-max distribution (the need-aware
policy is optimal for exactly this objective), and the gap is real: six ordinary
dice reach 4,000 in **7.82** need-aware turns versus **7.94** for points-max play,
and strong sets improve more. `g` at every standard wager (1,500 / 2,000 / 2,500 /
3,000 / 4,000) rides in each result, so the wager selector re-ranks instantly
without re-solving.

### The play-along advisor (need-aware)

The threshold table answers "when do I stop?", but the question the player
actually faces each throw is "*which dice* do I set aside?" — and the answer is
often unintuitive (roll `1,1,5,2,3,6` on a strong set and the optimum keeps a
single 1, not both 1s and the 5). The tables can't express that, so the UI has a
*Play along* mode: pick a set, tell it the wager and your banked score, enter
what each die shows after every throw, and it answers with the exact move — what
to set aside, then throw or bank.

Crucially the advice is **need-aware**, conditioned on the points you still need
to win (`need = wager − banked`), not on maximising this turn's points.
`advise(rows, mask, faces, t, need)` reads the same `U_r` table `need_curve`
solves: it looks up the legal set-asides in the move table and picks the one
**minimising** `U_need(remaining hand, t + points)` — the fewest expected turns
to win, the mirror of the old points-max argmax — then reads throw/bank off the
same table. The instant a set-aside carries the tally to `≥ need` it scores the
minimum `U = 1` (banking wins outright), so it is always chosen and banked. This
is the qualitatively new endgame behaviour: three hundred short of the line the
advisor banks the moment you cover it, where the points-max policy would gamble
on. Alongside each recommendation it shows the **chance of clearing the need this
very turn** under the advice — a forward pass over the solved `U_r`, exact, no
sampling — and the expected turns each option costs. A test audits 400 random
states, checking `advise` reproduces the `U_r` argmin *and* the same bank/roll
call the solver's own comparison makes; the two drifting apart is the exact bug
class this upgrade exists to kill.

The page tracks which physical dice remain (a bitmask), the round tally, and the
banked game total between calls; all three are editable so the tool can pick up a
game mid-way, and banking or winning rolls the total forward automatically.
Tables are cached per **(set, wager)**, and because the optimiser already solves
each set's need curve it **ships that curve to the page**, which hands it back on
open — so warming the advisor is ~30 ms (just the work lists) rather than the
~50 s a fresh full solve to 4,000 would cost. A single need is then re-solved on
demand, warm-started from the curve's own `g[need]`, in a sweep or two.

The strategy table above the advisor still describes the points-max policy (its
need-aware recomputation is a later task); it is worded in the game's own
vocabulary: rows count the dice you would *throw* (the ones you did not select),
and the score to compare is the round tally plus the current selection — the
middle and bottom numbers at the edge of the KCD2 table, added together.

## A result worth knowing

Bank thresholds for six fair dice, by dice left in hand:

| dice left | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| bank at | 300 | 250 | 450 | 1050 | 3400 | never |

The one-die threshold **breaks the pattern** — you should push your luck harder
with one die than with two, despite one die busting 67% of the time versus 44%.
Rolling your last die is the only way to trigger hot dice, so success refills the
whole hand. Rebuilding the table with hot dice removed restores the expected
monotone order (0 / 100 / 250 / 900 / 2900) and drops the turn value from 564 to
480 — so hot dice alone are worth ~84 points per turn.

## Layout

```
run.py               start the web app
tests.py             test suite
check_filter.py      audits the shortlist against brute force
farkle/
  rules.py           scoring, legal set-asides, move table
  dice.py            the 43 dice as exact integer weights
  solver.py          turn DP, first-roll EV, ranking, orchestration
  server.py          stdlib HTTP server + background job runner
  web/               index.html, app.js, style.css
    img/             die icons + game UI textures, both from the wiki
kcd2farkleoptimizer-main/   the original JS tool, unchanged
```

The panel backgrounds are the game's own textures rather than hand-made
approximations: the wiki skins itself with them in `MediaWiki:Common.css`, which
names `KCD2_pattern_blue_bg.jpg`, `Kcd2_sec_bg_extrawide.png` and
`Kcd2_item_border.png` along with the `border-image-slice: 310 270 310 270`
values the ornate frame needs.

The typefaces are the game's too: the wiki's `MediaWiki:Fonts.css` declares the
Kingdom Come family (Regular / Bold / Italic / Display) as woff2 files, served
in `farkle/web/fonts/` and mapped so weight and style resolve within one family
name. Headers use the Display cut in wide-tracked caps, matching how the game
titles its screens. Like the textures, these are Warhorse's assets via the
community wiki — fine for a personal tool, not for redistribution.

In the strategy table, each "you'd throw N dice" row unfolds in place to show
the exact threshold for every distinct hand of that size, so the detail lives
where the question ("which five?") arises. In the advisor, faces can be typed —
digits fill the dice top to bottom, Backspace undoes, Enter asks and then
advances to the next throw.

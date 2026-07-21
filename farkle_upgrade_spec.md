# Farkle optimiser upgrade: need-aware solving

## Context and motivation

The current solver's headline number, `turn_ev`, is the expected banked score under
the policy that maximises expected banked score, with banking forced only above
`BANK_CAP = 10,000`. For strong dice sets this policy is extremely aggressive
(hot-dice chains keep E[roll] above the tally for a long time), so the turn
distribution is a large mass at 0 (busts) plus a fat tail of huge runs. The mean
(~2500 for top sets) is exact but unrepresentative — the median turn can be 0 —
and the policy itself is wrong for real games, where the target is 1500–4000 and
points beyond the target are worthless. `turns_to_win` partially corrects this by
running a renewal over the banked distribution, but that distribution still comes
from the EV-max policy, and the live advisor reads off the same EV-max value
table, so mid-game it recommends rolling when banking would win.

This upgrade makes the solver *need-aware*: every decision is conditioned on the
points still required to reach the target. Everything stays exact — no Monte
Carlo anywhere. Work through the tasks in order; each leaves the app in a
working state. Run the existing test suite after every task and add the new
tests described in Task 6.

## Definitions

Let STEP = 50 as today. Let r be the remaining need (target minus points already
banked across the whole game), a multiple of STEP with r <= MAX_TARGET = 4000.

g[r] = expected number of turns to clear a remaining need of r, playing each
turn optimally *for that purpose*. g[r] = 0 for r <= 0.

Within one turn at need r, define U_r(hand, t) = expected total turns from this
decision point until the need is cleared (counting the current turn as 1),
where t is the tally set aside so far this turn and `hand` is the bitmask of
dice about to be rolled or available to roll. The Bellman equation:

- If t >= r: bank immediately. U = 1. (No reason ever to roll past the need.)
- Otherwise U_r(hand, t) = min( bank, roll ) where
  - bank = 1 + g[r - t]        (only legal if t > 0, i.e. at least one
                                scoring die has been set aside this turn;
                                the very first roll of a turn cannot be skipped)
  - roll = sum over outcomes of `hand`:
      - Farkle outcome: contributes P(outcome) * (1 + g[r])   <- self-reference
      - Scoring outcome: contributes P(outcome) * min over legal set-asides
        (next_hand, pts) of U_r(next_hand, t + pts), with hot dice refilling
        next_hand to FULL_HAND exactly as the existing move table already does.

Note the objective is a MINIMUM of expected turns (the existing solver
maximises points; do not copy the max by reflex).

## Why the recursion is solvable in one upward sweep plus a small fixed point

Banking b > 0 references g[r - b] with r - b < r, already known when r is swept
upward from 50. The only self-reference is the bust branch, which references
g[r] itself. Handle it by parameterising: let x be a guess for g[r], solve the
turn DP with the bust branch valued at 1 + x, and read off g'[r] =
U_r(FULL_HAND, 0). The map x -> g'[r] is a contraction (its slope is the bust
probability under the induced policy, which is < 1), so fixed-point iteration
converges geometrically. Iterate until |g' - x| < 1e-9; warm-start x with g[r -
STEP] (or the legacy renewal estimate) and expect ~3–6 iterations. Assert
convergence within a bounded iteration count (say 200) so a bug fails loudly.

Cost estimate, to set expectations: for each r the turn DP only needs t in
0..r (banking is forced at t >= r), so each solve is far smaller than today's
sweep to 10,000. Roughly (80 values of r) x (~4 fixed-point iterations) x
(small DP) per dice set — comparable to the current single big solve. Reuse
`_work_lists(dice_probs)` across all r and all iterations for a set; it does
not depend on r.

## Task 1 — Surface the turn distribution (do this first, it is nearly free)

`_banked_distribution(V, work)` already computes the exact per-turn banked
distribution under the EV-max policy but it is never shown. Add to each result
(and the UI): bust probability (mass at 0), median banked score, 25th/75th
percentiles, and the conditional mean given banking. Present `turn_ev` next to
these so the skew is visible, and label it clearly as "mean under unlimited
points-max play" rather than a score a player should expect. This is the
honesty fix independent of everything else.

## Task 2 — Implement the need-aware solver

New function in `farkle/solver.py`, e.g. `need_curve(dice_probs, max_target=4000,
work=None)`, returning:

- `g`: list indexed by r // STEP for r = 0..max_target (g[0] = 0), and
- enough per-r solved values to reconstruct the policy later (see Task 4). At
  minimum keep, for each r, the flat U_r array or a compact policy summary;
  memory for 80 small arrays per set is acceptable, but if it is not, keep only
  the r values the UI needs plus a way to re-solve one r on demand (each single
  r re-solve is cheap given `work` and the g curve).

Implementation mirrors `value_table`: same flat indexing, same move-table
groups, but (a) minimise, (b) sweep t downward only from r, (c) bust branch =
1 + x per the fixed point above, (d) the t = 0, hand = FULL_HAND entry has no
bank option. Keep the existing `value_table`/`turn_ev` intact for comparison
output; do not delete them.

Correctness invariants to assert in code or tests:
- g is non-decreasing in r.
- g[STEP] = 1 / P(turn banks at least 50 under the optimal-for-that-need
  policy); for r = 50 the optimal policy is "bank the first thing that
  scores", so this is checkable nearly by hand for the ordinary die.
- g[target] <= the legacy `turns_to_win[target]` for every set and target
  (the need-aware policy is optimal for exactly this objective, so it can
  never be beaten by the EV-max policy's renewal). Strict improvement is
  expected for strong sets; equality within tolerance is fine for weak ones.

## Task 3 — Make g[target] the ranking metric

`optimize()` currently sorts by `turns_to_win[DEFAULT_TARGET]` computed from the
EV-max distribution. Replace the sort key with need-aware g[target]. Make the
target a request parameter (UI control offering at least 1500, 2000, 2500,
3000, 4000; default 4000) threaded through `/api/optimize` and the front end.
Report the g values at all standard targets per set. Keep `turn_ev` and the
legacy `turns_to_win` in the payload as secondary/diagnostic figures.

The shortlist stage (first-roll EV ranking) stays, but re-run the
`check_filter.py` audit against the *new* objective: modify that script so the
"true best" is the set minimising g[target] (audit at both 1500 and 4000, since
the shortlist proxy may correlate differently at short and long targets), and
report the same shortlist-size table. If the audit shows the optimum falling
outside the default shortlist of 8 at either target, raise the default.

## Task 4 — Make the advisor need-aware

`advise()` (and the `/api/advise` endpoint plus the advisor UI) must accept the
remaining need r — equivalently, the target and both players' banked totals,
from which the page computes r for the player. The recommendation becomes: for
the rolled hand, choose the set-aside minimising U_r(next_hand, t + pts), then
the roll/bank decision reads off the same values; if t + pts >= r the advice is
always "bank" (it wins). Show alongside the advice the probability of clearing
the need this turn under the recommended play, which falls out of a forward
pass over the solved U_r (analogous to `_turn_shape`).

Cache per (set, target) rather than per set: `prepare()` should build `work`,
the g curve, and whatever per-r tables Task 2 retained. Keep the warm-up
behaviour so the first question is still instant.

Degenerate-input handling: if the client sends r larger than max_target, clamp
and note it; r <= 0 should never reach the solver (the game is over).

## Task 5 — Need-aware strategy summaries

`_bank_thresholds` and `_turn_shape` currently describe the EV-max policy.
Recompute them under the need-aware policy at three representative needs and
label them in the UI as such: far from the target (r = 2000 or the target if
smaller), mid-game (r = 1000), and endgame (r = 300). The endgame column will
show the qualitatively new behaviour (bank the moment the need is covered) and
is the piece players will actually use at the table. The existing "safe /
high" per-hand-size presentation carries over unchanged, just per need level.

## Task 6 — Tests

Add to `tests.py`:
- The three invariants from Task 2.
- Fixed-point convergence within the iteration bound for a spread of sets:
  all-ordinary, six Weighted dice (extreme 1s), a straight-oriented mix, and a
  set containing a die with impossible faces (e.g. Pie die) to exercise the
  zero-probability pruning under the min-objective.
- Advisor consistency: for a handful of hand/tally/need states, the action
  returned by `advise` equals the argmin the U_r table implies (guards against
  the advisor and solver drifting apart, which is the bug class this whole
  upgrade exists to kill).
- A regression pin: for six ordinary dice, g[2000] equals a hard-coded value
  computed once and reviewed (exact solver, so exact pin with 1e-6 tolerance).
- Distribution stats from Task 1: probabilities sum to 1, median <= conditional
  mean for right-skewed sets, bust mass matches `_turn_shape`'s bust total.

## Task 7 (optional, only if everything above is done and tested) — opponent race

Win-probability DP over (my remaining need, opponent remaining need) with
alternating turns: my turn draws from my need-aware banked distribution at my
current need (which requires the per-r banked distributions, a forward pass per
r as in Task 4), the opponent from a configurable model (default: six Ordinary
dice playing the same need-aware policy; optionally a boss preset with loaded
dice). Strictly speaking my optimal policy should react to the opponent's need
as well; implementing the simpler version above (each side plays
turns-minimising for its own need) is an accepted approximation — note it in
the README. Report P(win) for going first and second. Do not let this task
block or complicate Tasks 1–6; it touches nothing they touch except reusing
the per-r distributions.

## Non-goals

- No Monte Carlo anywhere; if something seems to need sampling, the design is
  wrong — ask before proceeding.
- Do not model the wildcard dice (Devil's head, Balatro); the existing
  exclusion stands.
- Do not change the scoring rules module; it is verified against the wiki.
- Keep the stdlib-only constraint of the server.

## Documentation

Update the README: explain the difference between `turn_ev` (mean under
unlimited points-max play), the distribution stats, and g[target] (expected
turns under need-aware play — the number that matches what a player
experiences and the metric sets are ranked by). A short worked example with one
strong set showing bust%, median, turn_ev, and g[4000] side by side would
preempt the exact confusion that motivated this upgrade.

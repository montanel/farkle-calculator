"""Exact solvers for KCD2 Farkle.

Two numbers are produced for a set of six dice:

  first_roll_ev  Expected points from one roll, taking the best available
                 scoring dice and stopping. Cheap. This is (a corrected
                 version of) what the original JS tool reported.

  turn_ev        Expected points banked in a whole turn under *optimal* play:
                 rerolling, choosing which scoring dice to keep, hot dice, and
                 busting are all modelled. This is the number that actually
                 answers "which dice should I use".

Both are exact -- no Monte Carlo anywhere.
"""

import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool

from . import solvecache
from .rules import (FULL_HAND, best_take, build_move_table,
                    outcome_probabilities, rules_signature)

# --- Discretisation -----------------------------------------------------------
# Every score in Farkle is a multiple of 50, so a 50-point grid loses nothing.
STEP = 50
BANK_CAP = 10_000          # above this the solver is forced to bank
MAX_SELECTION = 8_000      # six 1s, the largest single set-aside

NT = BANK_CAP // STEP + 1              # rows we actually solve: t = 0..10000
TAIL = MAX_SELECTION // STEP + 1       # padding so t+points never overruns
ROW = NT + TAIL

# The DP indexes V[hand * ROW + t_index + points_steps] with no bounds check --
# the tail exists precisely so that is always safe. Assert it rather than let a
# tweaked constant silently read a neighbouring hand's values.
assert (NT - 1) + MAX_SELECTION // STEP < ROW, "tail too short for the largest score"

# Every disk-cache key carries this: a cached solve is exact only for the rules
# and grid that produced it. `-vN` lets a change to the stored result's shape
# invalidate old entries without touching the scoring tables.
_CACHE_SIG = f"{rules_signature()}-{STEP}-{BANK_CAP}-{MAX_SELECTION}-v2"

_MOVES = None
_GROUPS = None


def moves():
    global _MOVES
    if _MOVES is None:
        _MOVES = build_move_table()
    return _MOVES


def groups():
    """Outcomes bucketed by the move-set they offer, per hand.

    Many different rolls leave you with exactly the same choice -- every roll
    whose only scoring die is the 5 on die #3, say, plays identically no matter
    what the other five dice show. Since the value of a move-set depends only on
    the move-set, such outcomes can be merged and their probabilities added.
    That collapses ~108k outcome evaluations per sweep to ~11k.

    The bucketing depends only on the rules, so it is computed once and shared
    by every dice combination; only the bucket probabilities are per-combo.

    Returns per hand: (singles, multis) where each is a list of
    (flat_offsets, [outcome_ids]).
    """
    global _GROUPS
    if _GROUPS is None:
        mv = moves()
        out = [None] * 64
        for hand in range(1, 64):
            singles, multis = {}, {}
            for oid, opts in enumerate(mv[hand]):
                if not opts:
                    continue  # Farkle: contributes 0, never needs looking up
                key = tuple(nxt * ROW + pts // STEP for nxt, pts in opts)
                bucket = singles if len(key) == 1 else multis
                bucket.setdefault(key, []).append(oid)
            out[hand] = (
                [(k[0], oids) for k, oids in singles.items()],
                list(multis.items()),
            )
        _GROUPS = out
    return _GROUPS


# --- The turn solver ----------------------------------------------------------

def _work_lists(dice_probs):
    """Per-hand move-sets paired with the probability of being offered them.

    Each entry is (probability, flat offset(s)). Offsets already encode
    `next_hand * ROW + points // STEP`, so adding `ti` lands on the successor
    state. Zero-probability buckets are dropped, which is a large saving for
    dice with impossible faces. Returns (single, multi, farkle_p).
    """
    grp = groups()
    single, multi, farkle = [None] * 64, [None] * 64, [0.0] * 64
    for hand in range(1, 64):
        probs = outcome_probabilities(hand, dice_probs)
        gs, gm = grp[hand]
        s, m, live = [], [], 0.0
        for off, oids in gs:
            p = sum(probs[o] for o in oids)
            if p:
                s.append((p, off))
                live += p
        for offs, oids in gm:
            p = sum(probs[o] for o in oids)
            if p:
                m.append((p, offs))
                live += p
        single[hand], multi[hand] = s, m
        farkle[hand] = 1.0 - live  # every outcome offering no move is a bust
    return single, multi, farkle


def value_table(dice_probs, work=None, nt=NT):
    """Solve V(hand, t) for six given dice and return the flat value array.

    `work` accepts an already-built `_work_lists` result so a caller that also
    runs the forward pass does not build it twice.

    V(hand, t) = max( bank t , expected value of rolling `hand` )

    Solved by backward induction on t. Every legal set-aside scores at least 50,
    so any reroll strictly increases t; sweeping t downwards therefore means
    every value the recursion asks for is already final. No iteration to
    convergence, no approximation -- one pass gives the exact answer.

    `nt` truncates the horizon: banking is forced at t >= nt*STEP rather than at
    the full cap. With nt = NT this is exact; a smaller nt gives a cheaper, slightly
    conservative turn_ev (it ignores the rare hot-dice runs that carry a tally past
    nt*STEP), used only as the stage-2 sieve where a faithful *ranking* -- not an
    exact value -- is all that is needed. The move offsets are unchanged, so the
    same tables and the same ROW serve both; only the forced-bank boundary moves.

    Index as V[hand * ROW + t // STEP].
    """
    # Flat value array: V[hand * ROW + t_index]. Flat because the inner loop
    # runs tens of millions of times and one list index beats two.
    V = [0.0] * (64 * ROW)
    for hand in range(64):
        base = hand * ROW
        for ti in range(nt, ROW):
            V[base + ti] = ti * STEP  # past the horizon: banking is forced

    # Per-hand work lists: each entry pairs a move-set with the total
    # probability of landing in a roll that offers it. Zero-probability buckets
    # are dropped, which is a large saving for dice with impossible faces.
    single, multi, _ = work if work is not None else _work_lists(dice_probs)

    # Stage 2 of the funnel runs this hundreds of times per solve, so the inner
    # loop earns the same hand-tuning as _need_sweep: a single-pass argmax instead
    # of building a list per move-group and re-scanning it with max(), plus a local
    # ROW so the hot indexing is LOAD_FAST.
    row = ROW
    for ti in range(nt - 1, -1, -1):
        t = ti * STEP
        for hand in range(1, 64):
            acc = 0.0
            for p, off in single[hand]:
                acc += p * V[off + ti]
            for p, offs in multi[hand]:
                mx = V[offs[0] + ti]
                for o in offs[1:]:
                    v = V[o + ti]
                    if v > mx:
                        mx = v
                acc += p * mx
            V[hand * row + ti] = acc if acc > t else t

    return V


def turn_ev(dice_probs, want_policy=False, names=None, work=None):
    """Expected points banked in one turn with six given dice, under optimal play.

    `names` labels each die so the policy can say which hand a threshold is for.
    `work` accepts a prebuilt `_work_lists` result so a caller that also runs the
    need-aware solve does not build it twice.
    """
    if work is None and want_policy:
        work = _work_lists(dice_probs)
    V = value_table(dice_probs, work)
    result = {"turn_ev": V[FULL_HAND * ROW]}
    if want_policy:
        result["bank_at"] = _bank_thresholds(V, names)
        result["shape"] = _turn_shape(V, dice_probs, work=work)
        result["turns_to_win"] = turns_to_win(V, work)
        result["turn_dist"] = turn_distribution(V, work)
    return result


def _turn_shape(V, dice_probs, tol=1e-9, work=None):
    """How a turn actually ends, by pushing probability through the solved policy.

    A threshold answers "at what score would I stop?", which says nothing about
    whether that score comes up. Holding three dice at 7,000 points may be
    correct *and* almost unreachable. So walk the turn forward from a full hand
    and record, for each hand size, how much probability mass ends there --
    banked or bust.

    Any move raises t, so a single upward sweep over t visits every state after
    all its inflow has arrived.

    Per hand size this yields "bank"/"bust" (shares of all turns ending there)
    and "bust_if_roll" -- the chance a roll of that many dice scores nothing,
    conditional on the policy choosing to roll. The two answer different
    questions and are easy to confuse: a hand can be the most common place to
    *finish* precisely because it is where you usually stop, while rolling it
    stays dangerous. Returns those plus the overall bust chance.
    """
    single, multi, farkle = work if work is not None else _work_lists(dice_probs)

    mass = [0.0] * (64 * ROW)
    mass[FULL_HAND * ROW] = 1.0  # every turn starts with six dice and no score
    shape = {n: {"bank": 0.0, "bust": 0.0, "rolled": 0.0, "bust_if_roll": None}
             for n in range(1, 7)}

    for ti in range(NT):
        for hand in range(1, 64):
            here = mass[hand * ROW + ti]
            if here <= 0.0:
                continue
            n = bin(hand).count("1")
            if V[hand * ROW + ti] <= ti * STEP + tol:
                shape[n]["bank"] += here      # the policy stops here
                continue
            shape[n]["rolled"] += here        # the policy rolls: risk is taken
            shape[n]["bust"] += here * farkle[hand]
            for p, off in single[hand]:
                mass[off + ti] += here * p
            for p, offs in multi[hand]:
                # The policy takes the set-aside with the highest value; ties go
                # to the first, exactly as the backward pass scored them.
                best, bv = offs[0], V[offs[0] + ti]
                for o in offs[1:]:
                    v = V[o + ti]
                    if v > bv:
                        best, bv = o, v
                mass[best + ti] += here * p

    # A roll can carry t past the cap into the padded tail, where banking is
    # forced. That mass is still a banked turn, so collect it rather than
    # letting it fall off the end.
    for hand in range(1, 64):
        n = bin(hand).count("1")
        base = hand * ROW
        for ti in range(NT, ROW):
            if mass[base + ti]:
                shape[n]["bank"] += mass[base + ti]

    # The decision-relevant risk: given the policy rolls this many dice, how
    # often does that roll score nothing? Weighted by how often each specific
    # hand actually comes up, since a 3-die bust chance depends on *which* three.
    for n, s in shape.items():
        s["bust_if_roll"] = s["bust"] / s["rolled"] if s["rolled"] > 0 else None

    bust = sum(s["bust"] for s in shape.values())
    total = bust + sum(s["bank"] for s in shape.values())
    assert abs(total - 1.0) < 1e-9, f"turn outcomes sum to {total}, not 1"
    return {"by_size": shape, "bust": bust}


# --- Turns to reach a wager target -------------------------------------------
# turn_ev says what a turn is worth in points; it does not say how many turns a
# whole game takes, which is what actually decides a wager. Two sets with the
# same turn_ev are not equal at the table: the one that busts more often needs
# more turns to cross the line, because every busted turn is a turn that banked
# nothing. That risk is what this metric prices in and turn_ev cannot.
#
# Model: play every turn for maximum points (the same optimal policy turn_ev
# already solves), which gives a per-turn distribution of banked scores q(b),
# with q(0) the bust chance. Reaching a remaining need of r is then a renewal:
#
#     g[r] = 1 + sum_b q(b) * g[r-b]           (g[r] = 0 once r <= 0)
#
# this turn (the +1) plus wherever it leaves you. Pulling g[r] out of its own
# bust term (b = 0 leaves the need unchanged):
#
#     g[r] = (1 + sum_{0<b<r} q(b) g[r-b]) / (1 - q(0))
#
# so every target falls out of a single upward sweep. Solving to the largest
# target answers the smaller ones for free -- g[2000] is just an earlier entry.

WIN_TARGETS = (2000, 3000, 4000)
DEFAULT_TARGET = 4000


def _banked_distribution(V, work):
    """Per-turn distribution of banked score under the optimal policy.

    Returns {score -> probability}, with score 0 the bust mass. Same forward
    walk as `_turn_shape`, only bucketed by how much a turn banks rather than by
    how many dice it ends holding.
    """
    single, multi, farkle = work
    mass = [0.0] * (64 * ROW)
    mass[FULL_HAND * ROW] = 1.0
    banked = {}
    for ti in range(NT):
        for hand in range(1, 64):
            here = mass[hand * ROW + ti]
            if here <= 0.0:
                continue
            if V[hand * ROW + ti] <= ti * STEP + 1e-9:      # the policy stops here
                banked[ti * STEP] = banked.get(ti * STEP, 0.0) + here
                continue
            banked[0] = banked.get(0, 0.0) + here * farkle[hand]
            for p, off in single[hand]:
                mass[off + ti] += here * p
            for p, offs in multi[hand]:
                best, bv = offs[0], V[offs[0] + ti]
                for o in offs[1:]:
                    v = V[o + ti]
                    if v > bv:
                        best, bv = o, v
                mass[best + ti] += here * p
    # A hot-dice run can carry the tally past the cap, where banking is forced.
    for hand in range(1, 64):
        base = hand * ROW
        for ti in range(NT, ROW):
            if mass[base + ti]:
                banked[ti * STEP] = banked.get(ti * STEP, 0.0) + mass[base + ti]
    return banked


def _quantile(items, total, p):
    """Inverse CDF of a discrete distribution: the smallest score whose
    cumulative mass reaches p * total.

    `items` is (score, probability) sorted ascending. Busts sit at score 0, so a
    set that busts more than half its turns yields a median of 0 -- which is the
    whole point of surfacing this, since the mean can be many times a median of
    nothing.
    """
    threshold = p * total
    acc = 0.0
    for score, prob in items:
        acc += prob
        if acc >= threshold - 1e-12:
            return score
    return items[-1][0] if items else 0


def turn_distribution(V, work):
    """Shape of the per-turn banked score, not just its mean.

    `turn_ev` is the mean of exactly this distribution, and for strong sets that
    mean is wildly unrepresentative: a large mass busts (banks 0) while a fat
    tail of hot-dice runs drags the average far above what a typical turn banks.
    Reporting the bust probability, the quartiles (busts counted as a banked 0,
    so an over-half bust rate shows a median of 0) and the mean *conditional on
    banking anything* makes the skew visible instead of hiding it behind one
    optimistic number.

    Exact throughout -- a bucketed forward pass over the same policy, no sampling.
    """
    banked = _banked_distribution(V, work)
    total = sum(banked.values())
    bust = banked.get(0, 0.0)
    mean = sum(score * p for score, p in banked.items())
    live = total - bust                      # mass that banks something
    items = sorted(banked.items())
    return {
        "bust": bust,
        "p25": _quantile(items, total, 0.25),
        "median": _quantile(items, total, 0.50),
        "p75": _quantile(items, total, 0.75),
        "mean": mean,                        # == turn_ev, by construction
        "cond_mean": mean / live if live > 0 else None,
    }


def turns_to_win(V, work, targets=WIN_TARGETS):
    """Expected turns to bank each target total, playing every turn for points.

    A cheap renewal over the per-turn banked distribution; `targets` need not be
    sorted. Returned dict is keyed by the target values passed in.
    """
    banked = _banked_distribution(V, work)
    live = 1.0 - banked.get(0, 0.0)          # chance a turn banks anything at all
    if live <= 0.0:                          # no real die can never score, but be safe
        return {t: float("inf") for t in targets}
    hi = max(targets)
    g = [0.0] * (hi // STEP + 1)             # g[i] = expected turns to need i*STEP
    for ri in range(1, len(g)):
        r = ri * STEP
        acc = 1.0
        for b, q in banked.items():
            if 0 < b < r:
                acc += q * g[(r - b) // STEP]
        g[ri] = acc / live
    return {t: g[t // STEP] for t in targets}


# --- Need-aware turns-to-clear solver -----------------------------------------
# turns_to_win renews over the EV-max policy's banked distribution, but that is
# the wrong policy: it plays every turn for maximum points even when the game
# only needs a few hundred more to win. This solver instead answers, for each
# remaining need r, the expected number of turns to clear it under the policy
# that MINIMISES that number -- every decision conditioned on how much is still
# wanted, and never rolling past the need.
#
# g[r] = expected turns to clear a remaining need of r (g[r] = 0 for r <= 0).
# Within a turn at need r, U_r(hand, t) = expected total turns from here (this
# turn counted as 1) until the need clears, with t the tally set aside so far and
# `hand` the dice about to be rolled:
#
#   t >= r:  U = 1                        bank now and win; no reason to roll on
#   else:    U = min(bank, roll)
#     bank = 1 + g[r - t]                 only when t > 0 (the first roll can't be skipped)
#     roll = P(farkle) * (1 + g[r])       a bust ends the turn, needing g[r] more
#          + sum over scoring outcomes P(o) * min over set-asides U_r(next, t+pts)
#
# Sweeping r upward makes every g[r-t] (t > 0) already known; the sole self-
# reference is the bust term's g[r]. Parameterise it: guess x for g[r], value a
# bust at 1 + x, solve the turn DP, read g'[r] = U_r(FULL_HAND, 0). For a fixed
# induced policy the map x -> g'[r] is affine, g'[r] = a + b*x, with slope b =
# P(bust during the turn) < 1 -- so one sweep that also accumulates b jumps
# straight to the fixed point g[r] = (g'[r] - b*x) / (1 - b). Iterate until the
# policy stops shifting (a couple of sweeps, warm-started by extrapolating the
# curve); b < 1 guarantees convergence, asserted within a bounded count.

MAX_TARGET = 4000
# The game's four wagers map to these point targets: 1500 (bet 5), 2000 (10),
# 3000 (50), 4000 (130). There is no wager at 2500, so it is not among them.
NEED_TARGETS = (1500, 2000, 3000, 4000)
# The endgame the card reports a bust rate for: the last stretch to the line, where
# the need-aware policy banks the instant the tally covers what is left.
ENDGAME_NEED = 600
NEED_ITER_CAP = 200


def _need_sweep(single, multi, farkle, g, ri, bustval, U, B):
    """One backward sweep of the need-r turn DP at a fixed bust value.

    Writes U[hand*ROW+ti] = U_r(hand, t) and B[hand*ROW+ti] = dU/d(bust value) =
    the probability of busting before the need resolves, for ti in [0, ri). Cells
    at ti >= ri must already hold the boundary (U = 1, B = 0: banked and won); the
    sweep never writes them and relies on reading them as such. Mirrors
    `value_table`'s flat indexing and move groups, but minimises and values the
    bust branch at `bustval`. Returns (U, B) at (FULL_HAND, 0).
    """
    # Locals for the ~20M-iteration inner loop: LOAD_FAST beats LOAD_GLOBAL, and
    # tracking the argmin in one pass avoids building a list per move-group and
    # re-scanning it with min()/index() (together ~30% of the solve).
    row = ROW
    for ti in range(ri - 1, -1, -1):
        gbank = 1.0 + g[ri - ti] if ti > 0 else None   # bank value, constant in bust
        for hand in range(1, 64):
            fk = farkle[hand]
            ru = fk * bustval          # farkle: ends the turn worth 1 + g[r]
            rb = fk                    # ...and carries all of the bust sensitivity
            for p, off in single[hand]:
                idx = off + ti
                ru += p * U[idx]
                rb += p * B[idx]
            for p, offs in multi[hand]:
                bidx = offs[0] + ti     # need-aware: fewest expected turns, not most points
                mu = U[bidx]
                for o in offs[1:]:
                    j = o + ti
                    v = U[j]
                    if v < mu:
                        mu = v
                        bidx = j
                ru += p * mu
                rb += p * B[bidx]
            k = hand * row + ti
            if gbank is not None and gbank <= ru:   # banking is (weakly) better: stop
                U[k] = gbank
                B[k] = 0.0
            else:
                U[k] = ru
                B[k] = rb
    fb = FULL_HAND * row
    return U[fb], B[fb]


def solve_need(work, g, ri, warm, U=None, B=None, tol=1e-9):
    """Fixed-point solve for g[ri] given g[0..ri-1]; returns (g_ri, iters, bust, U).

    `warm` is a starting guess for g[ri]. `U`/`B` may be reused arrays whose
    ti >= ri cells hold the boundary (1.0 / 0.0); pass none and they are built.
    `bust` is the converged slope B_full = P(a turn started at need r ends in a
    bust) -- the mass at 0 of that turn's banked distribution. The returned U is
    the converged U_r table -- the advisor reads it to pick moves -- so a single r
    can be re-solved on demand rather than stored for all r.
    """
    single, multi, farkle = work
    if U is None:
        U = [1.0] * (64 * ROW)
        B = [0.0] * (64 * ROW)
    # Policy iteration: each sweep derives the greedy policy at the current bust
    # guess while accumulating B = dU/d(bust), so the affine fixed point closes
    # that policy exactly in one step; the next sweep re-derives the policy off the
    # corrected value, and it stops the moment the policy holds. Warm-started by the
    # caller's `warm` (an extrapolation of the curve), that is 2-3 sweeps in practice.
    guess = warm
    for it in range(1, NEED_ITER_CAP + 1):
        uf, bf = _need_sweep(single, multi, farkle, g, ri, 1.0 + guess, U, B)
        gnew = (uf - bf * guess) / (1.0 - bf)      # exact fixed point for this policy
        if abs(gnew - guess) < tol:
            return gnew, it, bf, U
        guess = gnew
    raise AssertionError(
        f"need fixed point did not converge for r={ri * STEP} within "
        f"{NEED_ITER_CAP} iterations (last step {abs(gnew - guess):.2e})")


def need_curve(dice_probs, max_target=MAX_TARGET, work=None):
    """Expected turns to clear every remaining need r = 0..max_target.

    Returns {"g": [...], "iters": [...], "bust": [...]} indexed by r // STEP:
    g[.] the need-aware expected turns (g[0] = 0), iters[.] the fixed-point sweeps
    that r cost (small and bounded), and bust[.] = P(a turn started at need r ends
    in a bust) under the need-aware policy (bust[0] = 0). A single r's policy is
    reconstructed on demand with `solve_need`, cheap given `work` and g -- far
    lighter than keeping 80 U tables per set. Keeps `value_table`/`turn_ev`
    untouched for side-by-side comparison.
    """
    if work is None:
        work = _work_lists(dice_probs)
    n = max_target // STEP
    g = [0.0] * (n + 1)
    iters = [0] * (n + 1)
    bust = [0.0] * (n + 1)
    # One pair of arrays reused across every r: r is swept upward, so a cell above
    # the current need was only ever the untouched boundary and is safe to read.
    U = [1.0] * (64 * ROW)
    B = [0.0] * (64 * ROW)
    for ri in range(1, n + 1):
        # The curve bends gently, so a linear extrapolation lands in the right
        # policy region and keeps the sweep count near its two-sweep floor.
        if ri >= 3:
            warm = max(g[ri - 1], 2.0 * g[ri - 1] - g[ri - 2])
        elif ri == 2:
            warm = g[1]
        else:
            warm = 1.0
        g[ri], iters[ri], bust[ri], _ = solve_need(work, g, ri, warm, U, B)
        assert g[ri] >= g[ri - 1] - 1e-9, \
            f"g not non-decreasing at r={ri * STEP}: {g[ri - 1]} -> {g[ri]}"
    return {"g": g, "iters": iters, "bust": bust}


def _init_worker(grouped):
    """Seed a fresh worker with the already-built move table.

    Windows spawns bare interpreters, so without this each worker would rebuild
    the table from scratch -- about 2s apiece, and they all pay it at once at
    the start of the run. Shipping the finished table costs 7ms to unpickle
    instead, because `groups()` short-circuits once `_GROUPS` is populated.
    """
    global _GROUPS
    _GROUPS = grouped


def _solve_one(job):
    """One fully-solved set. Module-level and picklable so it can run in a pool.

    Each set is independent, so the exact solve parallelises perfectly. Builds the
    work lists once and shares them between the points-max turn solve (turn_ev and
    its policy tables) and the need-aware curve.
    """
    index, rows, names, max_target = job
    work = _work_lists(rows)
    res = turn_ev(rows, want_policy=True, names=names, work=work)
    res["need"] = need_curve(rows, max_target=max_target, work=work)
    # Everything the set card shows, under the need-aware policy at each wager it
    # might be read at: win-this-turn, throw risk by hand size, and the bank
    # thresholds that surface as "exceptions". Supersedes the old far/mid/endgame
    # policy tables (need_strategy stays available for the audit/tests).
    res["card"] = need_card(work, res["need"]["g"], res["need"]["bust"],
                            max_target, names=names)
    return index, res


# The stage-2 sieve solves turn_ev only to a truncated horizon, not the full 10,000
# cap -- cheaper, and all the sieve needs is a faithful *ranking*. The horizon must
# reach the target being ranked for, though: the need-aware policy banks the instant
# the tally covers the target, and points past it never help g, so a turn_ev that
# forces banking AT the target is the closest cheap proxy to g. A horizon set *below*
# the target (an earlier build's flat 3,000) under-ranks big-turn sets and dropped
# the g-optimum from the shortlist at target 4,000 -- audited at turn_ev rank #11
# (dropped) truncated at 3,000 vs #2 with a target-aligned horizon. The finalists
# are re-solved exactly (to the full need curve) in stage 3, so this never reaches a
# reported number.
def sieve_nt(target):
    """Rows the stage-2 turn_ev sieve solves for a given ranking target: enough to
    reach the target (banking is forced there), capped at the full horizon."""
    return min(NT, target // STEP + 1)


def _turn_ev_one(job):
    """A cheap, target-horizon turn_ev -- stage 2 of the funnel. Module-level so it
    runs in the pool.

    `job` is `(index, rows[, nt])`; `nt` is the sieve horizon (defaults to the full
    cap for direct callers such as the audit). Cheap next to a full need curve (a
    fraction of a second vs tens of seconds), it narrows the first-roll shortlist to
    the handful worth a full solve, and correlates with the need-aware objective far
    better than first-roll value -- it already prices in rerolls, hot dice and busting.
    """
    index, rows = job[0], job[1]
    nt = job[2] if len(job) > 2 else NT
    return index, value_table(rows, nt=nt)[FULL_HAND * ROW]


def _need_targets(job):
    """Need-aware g at the given targets for one set. Module-level for the audit
    pool (see check_filter.py); one need_curve to the largest target answers all."""
    rows, targets = job
    g = need_curve(rows, max_target=max(targets))["g"]
    return {t: g[t // STEP] for t in targets}


_POOL = None
_POOL_LOCK = threading.Lock()


def default_workers():
    # Each set is now a ~40s exact need-curve solve, so per-task coordination is
    # noise and more cores pay off -- unlike the old ~1s value-table jobs, where a
    # low cap made sense. Cap at 16 to bound worker memory (each holds the move
    # table) on very large machines.
    return max(1, min(16, os.cpu_count() or 1))


def shared_pool(workers=None):
    """A process pool that outlives one request, created on first use.

    Spawning eight interpreters costs a few seconds on Windows, which is dead
    time the user watches. The server is long-lived and every solve wants the
    same warm workers, so the pool is built once and kept. `warm_up` primes it
    during startup so even the first solve skips the wait.
    """
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ProcessPoolExecutor(
                max_workers=workers or default_workers(),
                initializer=_init_worker, initargs=(groups(),))
        return _POOL


def _discard_pool():
    """Drop a pool that has broken so the next call builds a fresh one."""
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.shutdown(wait=False)


def warm_up():
    """Build the move table and start the workers before anyone asks."""
    groups()
    pool = shared_pool()
    list(pool.map(int, [0] * default_workers()))   # force every worker to boot


def _map_pool(fn, jobs, stage, note, base_done, total, workers=None):
    """Run `fn` over `jobs`, each `(index, *payload)`, returning [(index, value)].

    `stage`/`base_done`/`total` drive one continuous progress count that a caller
    can carry across several stages. Serial when there is too little to pay for a
    pool, and a serial fallback if the pool cannot start -- a sandbox with no
    process spawning should give slower answers, not no answers.
    """
    n = len(jobs)
    if n == 0:
        return []
    if base_done + n <= 2 or (workers is not None and workers == 1):
        out = []
        for k, job in enumerate(jobs):
            out.append(fn(job))
            note(stage, base_done + k + 1, total)
        return out
    try:
        pool = shared_pool(workers)
        # submit + as_completed rather than map: map yields in submission order,
        # so one slow set holds back the count for every set finished behind it.
        futures = [pool.submit(fn, job) for job in jobs]
        out, done = [], base_done
        for fut in as_completed(futures):
            out.append(fut.result())
            done += 1
            note(stage, done, total)
        return out
    except (OSError, RuntimeError, ImportError, BrokenProcessPool):
        _discard_pool()
        out = []
        for k, job in enumerate(jobs):
            out.append(fn(job))
            note(stage, base_done + k + 1, total)
        return out


def _solve_stage(kind, fn, entries, stage, note, total, base_done=0,
                 max_target=0, workers=None):
    """Serve `entries` from the disk cache where possible, solve the rest in the pool.

    entries: [(index, names, job), ...] with `names` the sorted die-name cache key
    and `job` the `(index, *payload)` the pool function takes. Returns {index:
    value}. Cache reads and writes happen here in the main process -- workers never
    touch disk -- so a broken pool or an interrupted run cannot corrupt the store.
    """
    out, misses = {}, []
    for index, names, job in entries:
        hit = solvecache.get(kind, _CACHE_SIG, names, max_target)
        if hit is None:
            misses.append((index, names, job))
        else:
            out[index] = hit
    done = base_done + (len(entries) - len(misses))
    note(stage, done, total)   # account for cache hits up front
    keymap = {index: names for index, names, _ in misses}
    for index, value in _map_pool(fn, [job for _, _, job in misses],
                                  stage, note, done, total, workers):
        solvecache.put(kind, _CACHE_SIG, keymap[index], value, max_target)
        out[index] = value
    return out


def _hand_threshold(V, hand, tol=1e-9):
    """Lowest turn score at which banking beats rolling this exact hand.

    None means rolling stays worthwhile all the way to the bank cap.
    """
    base = hand * ROW
    for ti in range(NT):
        if V[base + ti] <= ti * STEP + tol:  # banking is (weakly) optimal
            return ti * STEP
    return None


def _threshold_summary(thresh, names=None):
    """Assemble the by_size / hands stop-threshold summary from a per-hand
    threshold function `thresh(hand)`.

    The threshold depends on *which* dice are left, not just how many, so this
    reports two views:

      by_size  {n: {"safe": x, "high": y}} -- `safe` is the lowest threshold
               across every hand of that size, so following it never rolls past
               the optimal point for the hand you actually hold. `high` is the
               largest. Either may be null, meaning rolling on is always right.

      hands    one entry per specific hand, naming the dice, for when you want
               the exact number rather than the safe one.

    Shared by the points-max reading (threshold off V) and the need-aware one
    (threshold off the solved U_r), so the two present identically.
    """
    by_size, seen, hands = {}, set(), []
    for hand in range(1, 64):
        n = bin(hand).count("1")
        th = thresh(hand)
        by_size.setdefault(n, []).append(th)
        if not names:
            continue
        # Two copies of the same die are interchangeable, so hands that differ
        # only in which copy is held are one row as far as the player is concerned.
        held = sorted(names[i] for i in range(6) if hand >> i & 1)
        key = tuple(held)
        if key in seen:
            continue
        seen.add(key)
        hands.append({"n": n, "dice": held, "bank": th})

    summary = {}
    for n, vals in by_size.items():
        known = [v for v in vals if v is not None]
        summary[n] = {
            "safe": min(known) if known else None,
            "high": (max(known) if known and len(known) == len(vals) else None),
        }

    hands.sort(key=lambda h: (-h["n"], h["bank"] is None, h["bank"] or 0))
    return {"by_size": summary, "hands": hands}


def _bank_thresholds(V, names=None):
    """When to stop under the points-max policy, summarised and hand by hand."""
    return _threshold_summary(lambda hand: _hand_threshold(V, hand), names)


# --- Need-aware strategy summaries --------------------------------------------
# The two readings above (bank thresholds, turn shape) describe the points-max
# policy. These recompute them under the need-aware policy at a fixed remaining
# need r, from the solved U_r table. The qualitatively new behaviour is the
# endgame one: the moment the tally covers r the policy banks (it wins), so the
# thresholds collapse toward r itself instead of the points-max policy's high,
# rarely-met numbers.

def _need_hand_threshold(U, g, ri, hand, tol=1e-9):
    """Lowest tally at which the need-aware policy banks this exact hand.

    Unlike the points-max threshold this is never None: banking is forced once
    the tally reaches the need (that wins), so a hand the policy never stops
    short with reports r itself. U[k] equals the bank value 1 + g[r - t] exactly
    when banking was chosen, so a tolerance-guarded compare recovers the choice.
    """
    base = hand * ROW
    for ti in range(1, ri):
        if U[base + ti] >= 1.0 + g[ri - ti] - tol:   # banking weakly optimal here
            return ti * STEP
    return ri * STEP        # only ever stops once the tally covers the need


def _need_bank_thresholds(U, g, ri, names=None):
    """When to stop under the need-aware policy at need r = ri*STEP.

    Same 'safe / high' per-hand-size presentation as the points-max thresholds,
    but every value is <= r: the policy banks the instant the need is covered."""
    return _threshold_summary(
        lambda hand: _need_hand_threshold(U, g, ri, hand), names)


def _need_turn_shape(U, work, g, ri):
    """How a turn ends under the need-aware policy at need r = ri*STEP.

    The mirror of `_turn_shape`: push probability forward from a full hand,
    following the policy's own bank/roll choice (bank iff 1 + g[r-t] <= roll
    value) at each state, and record where the mass settles by hand size. Mass a
    scoring set-aside carries to a tally >= r has *won* -- banked holding the
    successor hand -- so it is attributed there, exactly as the points-max shape
    counts a run that reaches the cap. Any move raises the tally, so one upward
    sweep visits each state after its inflow.
    """
    single, multi, farkle = work
    bustval = 1.0 + g[ri]
    mass = [0.0] * (64 * ROW)
    mass[FULL_HAND * ROW] = 1.0
    shape = {n: {"bank": 0.0, "bust": 0.0, "rolled": 0.0, "bust_if_roll": None}
             for n in range(1, 7)}
    win = 0.0   # mass that reaches a tally >= r: the need cleared this very turn
    for ti in range(ri):
        gbank = 1.0 + g[ri - ti] if ti > 0 else None
        for hand in range(1, 64):
            here = mass[hand * ROW + ti]
            if here <= 0.0:
                continue
            n = bin(hand).count("1")
            ru = farkle[hand] * bustval
            for p, off in single[hand]:
                ru += p * U[off + ti]
            for p, offs in multi[hand]:
                ru += p * min(U[o + ti] for o in offs)
            if gbank is not None and gbank <= ru:      # policy banks here (short of the need)
                shape[n]["bank"] += here
                continue
            shape[n]["rolled"] += here
            shape[n]["bust"] += here * farkle[hand]
            for p, off in single[hand]:
                dst = off + ti
                if dst % ROW >= ri:                    # tally covers the need: banked win
                    shape[bin(dst // ROW).count("1")]["bank"] += here * p
                    win += here * p
                else:
                    mass[dst] += here * p
            for p, offs in multi[hand]:
                best_o, best_u = offs[0], U[offs[0] + ti]
                for o in offs[1:]:
                    u = U[o + ti]
                    if u < best_u:
                        best_o, best_u = o, u
                dst = best_o + ti
                if dst % ROW >= ri:
                    shape[bin(dst // ROW).count("1")]["bank"] += here * p
                    win += here * p
                else:
                    mass[dst] += here * p
    for s in shape.values():
        s["bust_if_roll"] = s["bust"] / s["rolled"] if s["rolled"] > 0 else None
    bust = sum(s["bust"] for s in shape.values())
    total = bust + sum(s["bank"] for s in shape.values())
    assert abs(total - 1.0) < 1e-9, f"need-aware turn outcomes sum to {total}, not 1"
    return {"by_size": shape, "bust": bust, "win": win}


def need_strategy(dice_probs, g, levels, work=None, names=None):
    """Bank thresholds and turn shape under the need-aware policy at several needs.

    `levels` is [(label, r), ...]; for each r within the solved curve it re-solves
    U_r (cheap, warm-started from the curve's own g[r]) and reports the same
    'safe / high' thresholds and turn shape the points-max policy exposes, now
    conditioned on r. Returns [{label, need, bank_at, shape}, ...], the far / mid /
    endgame columns the UI labels -- the endgame one showing the bank-on-cover
    behaviour that is the whole point of the upgrade.
    """
    if work is None:
        work = _work_lists(dice_probs)
    n = len(g) - 1
    out = []
    for label, r in levels:
        ri = r // STEP
        if ri < 1 or ri > n:
            continue
        _, _, _, U = solve_need(work, g, ri, g[ri])
        out.append({
            "label": label,
            "need": r,
            "bank_at": _need_bank_thresholds(U, g, ri, names),
            "shape": _need_turn_shape(U, work, g, ri),
        })
    return out


# --- Need-aware set-card figures ----------------------------------------------
# What the set card shows, all under the need-aware policy at the selected wager
# (never the points-max policy): the chance a turn started at that wager banks the
# whole thing, the per-hand-size risk of a throw, and whether the policy ever banks
# short of the need (an "exception" -- weak sets do, strong sets don't).

def _card_needs(max_target):
    """Needs to precompute card figures for: every standard wager solved, plus the
    cap itself, so the card has stats for whichever wager the UI settles on."""
    needs = [t for t in NEED_TARGETS if t <= max_target]
    if max_target not in needs:
        needs.append(max_target)
    return sorted(set(needs))


def need_card(work, g, bust, max_target, names=None):
    """Per-wager card figures under the need-aware policy.

    For each need r in `_card_needs`, re-solve U_r (warm-started, ~2 sweeps) and
    read off, in a single forward pass, the chance the wager clears this turn and
    the per-hand-size throw risk; the bust curve gives the aggression far from the
    target and its collapse near it. Bank thresholds below the need become the
    "exceptions" the card lists instead of a table -- for a set that never banks
    short (thresholds all equal the need) there are none, and the card is one line.

    Returns {win1, bust_far, bust_end, roll_risk, strategy}, each keyed by wager
    where per-wager. `bust_end` is the endgame (r = 600, the last stretch where the
    play banks the moment the need is covered) bust, wager-independent.
    """
    win1, bust_far, roll_risk, exceptions = {}, {}, {}, []
    for r in _card_needs(max_target):
        ri = r // STEP
        _, _, _, U = solve_need(work, g, ri, g[ri])
        shape = _need_turn_shape(U, work, g, ri)
        win1[r] = shape["win"]
        bust_far[r] = bust[ri]
        roll_risk[r] = {n: shape["by_size"][n]["bust_if_roll"] for n in range(1, 7)}
        for n, v in _need_bank_thresholds(U, g, ri, names)["by_size"].items():
            if v["safe"] is not None and v["safe"] < r:   # banks short of the need
                exceptions.append({"n": n, "need": r, "bank": v["safe"]})
    end_i = ENDGAME_NEED // STEP
    bust_end = bust[end_i] if end_i < len(bust) else bust[-1]
    return {
        "win1": win1,
        "bust_far": bust_far,
        "bust_end": bust_end,
        "roll_risk": roll_risk,
        "strategy": {"simple": not exceptions, "exceptions": exceptions},
    }


# --- First-roll expected value ------------------------------------------------

_BT_CACHE = {}


def _bt6(counts):
    """best_take for a 6-die count vector, memoised on the hot path."""
    v = _BT_CACHE.get(counts)
    if v is None:
        v = _BT_CACHE[counts] = best_take(counts)
    return v


def first_roll_ev(dice_probs):
    """Exact expected score of a single 6-dice roll, taking the maximum available."""
    probs = outcome_probabilities(FULL_HAND, dice_probs)
    ev = 0.0
    for oid, p in enumerate(probs):
        if p == 0.0:
            continue
        counts = [0] * 6
        n = oid
        for _ in range(6):
            counts[n % 6] += 1
            n //= 6
        ev += p * _bt6(tuple(counts))
    return ev


# --- Stage 1: rank every 6-dice combination by first-roll EV -------------------

_BT5 = {}


def _bt5(cv):
    """For a 5-die count vector, best_take of it plus each possible 6th face."""
    v = _BT5.get(cv)
    if v is None:
        row = []
        for f in range(6):
            c = list(cv)
            c[f] += 1
            row.append(best_take(tuple(c)))
        v = _BT5[cv] = tuple(row)
    return v


def count_multisets(caps, size=6):
    """How many size-`size` selections exist from a pool with per-die caps."""
    ways = [1] + [0] * size
    for cap in caps:
        nxt = [0] * (size + 1)
        for have in range(size + 1):
            if not ways[have]:
                continue
            for take in range(min(cap, size - have) + 1):
                nxt[have + take] += ways[have]
        ways = nxt
    return ways[size]


def rank_combinations(pool, progress=None):
    """First-roll EV of every legal 6-dice selection, best first.

    `pool` is a list of (probs, cap) -- each distinct die and how many copies are
    available. Results are (ev, counts) where counts is a per-die tuple summing
    to six, so owning three copies of a die naturally allows using three of them.

    Walks selections as a DFS in non-decreasing die order, which visits every
    multiset exactly once and lets the face-count distribution of a shared prefix
    be computed once instead of once per selection. At depth 5 the distribution
    collapses into a 6-entry vector, after which each candidate for the last slot
    costs only six multiplies.
    """
    m = len(pool)
    probs = [p for p, _ in pool]
    caps = [c for _, c in pool]

    # How many dice are still reachable from index i onwards, for pruning.
    suffix = [0] * (m + 1)
    for i in range(m - 1, -1, -1):
        suffix[i] = suffix[i + 1] + caps[i]

    results = []
    taken = [0] * m
    seen = 0
    total = max(count_multisets(caps, 5), 1)

    def recurse(start, depth, dist):
        nonlocal seen
        if depth == 5:
            w = [0.0] * 6
            for cv, p in dist.items():
                b = _bt5(cv)
                w[0] += p * b[0]
                w[1] += p * b[1]
                w[2] += p * b[2]
                w[3] += p * b[3]
                w[4] += p * b[4]
                w[5] += p * b[5]
            for i in range(start, m):
                if taken[i] >= caps[i]:
                    continue
                pr = probs[i]
                ev = (pr[0] * w[0] + pr[1] * w[1] + pr[2] * w[2]
                      + pr[3] * w[3] + pr[4] * w[4] + pr[5] * w[5])
                taken[i] += 1
                results.append((ev, tuple(taken)))
                taken[i] -= 1
            seen += 1
            if progress and seen % 256 == 0:
                progress(seen, total)
            return

        for i in range(start, m):
            if taken[i] >= caps[i]:
                continue
            # Enough dice must remain from here on to fill the rest of the hand.
            if (caps[i] - taken[i] - 1) + suffix[i + 1] < 5 - depth:
                continue
            pr = probs[i]
            nxt = {}
            for cv, p in dist.items():
                for f in range(6):
                    pf = pr[f]
                    if pf:
                        c = list(cv)
                        c[f] += 1
                        key = tuple(c)
                        nxt[key] = nxt.get(key, 0.0) + p * pf
            taken[i] += 1
            recurse(i, depth + 1, nxt)  # `i`, not i+1: copies of a die may repeat
            taken[i] -= 1

    recurse(0, 0, {(0,) * 6: 1.0})
    results.sort(key=lambda r: -r[0])
    return results


# --- Orchestration ------------------------------------------------------------
# The shortlist is a funnel, not a single cut, because first-roll value is a poor
# proxy for the need-aware objective: on a large inventory it floods the top with
# near-duplicate strong sets and evicts genuinely better ones before they are ever
# solved. So narrow in two exact steps of rising cost. g depends only on a set's
# own six dice, never on the inventory, so the true best can only improve as the
# chest grows -- a reported best that gets *worse* on a bigger inventory is a
# shortlist miss, and this funnel is what keeps that from happening.
#
#   first-roll EV (all sets, ~free)   -> keep first_roll_keep(total) (scales)
#   turn_ev sieve  (~25 ms each)      -> keep `shortlist` (defaults to refine;
#                                        correlates with g far better -- it prices
#                                        in rerolls/hot-dice/bust)
#   need curve     (tens of seconds)  -> the full solve, then rank by g[target]

# first-roll EV -> turn_ev sieve. The g-optimum can rank surprisingly deep on
# first-roll value: adding copies of a strong die floods the top with near-duplicate
# high-EV sets and pushes a genuinely better, lower-EV set far down (audited at #625
# of ~9,800 -- 6.4% -- when three copies were added). That depth tracks the number
# of flooding sets, which grows with the chest, so a *fixed* cut silently drops the
# best on big inventories. Instead the net scales: a fraction of all sets, with a
# floor for small chests and a cap that bounds the sieve on huge ones. The sieve is
# cheap (~25 ms/set on the pool), so even a few thousand stays inside the time budget.
FIRST_ROLL_KEEP = 800          # floor: always sieve at least this many (or all, if fewer)
FIRST_ROLL_FRACTION = 0.15     # ...or this share of all sets, whichever is larger,
FIRST_ROLL_CAP = 3000          # ...but never more than this (bounds sieve time)


def first_roll_keep(total):
    """How many first-roll-EV leaders enter the turn_ev sieve, for `total` sets.

    Scales with the inventory so the flooding case above cannot bury the optimum,
    while the cap keeps the sieve affordable on the largest chests.
    """
    return min(total, FIRST_ROLL_CAP,
               max(FIRST_ROLL_KEEP, int(FIRST_ROLL_FRACTION * total)))


def optimize(inventory, dice_lookup, refine=20, progress=None, fill_die=None,
             target=DEFAULT_TARGET, max_target=None,
             prefilter=None, shortlist=None):
    """Rank every legal 6-dice selection through a three-stage funnel.

    inventory: {die name: how many you own}.
    dice_lookup: name -> [p1..p6], probabilities summing to 1.
    fill_die: the die the game puts in any slot you do not fill yourself. It is
        always available up to a full hand regardless of how many you own, so
        selections using fewer than six special dice are considered too.
    target: the wager the sets are ranked for -- fewest need-aware turns (g at
        `target`) wins. Every standard target up to `max_target` is reported.
    max_target: how far to solve the need curve; defaults to `target`. Points past
        the wager never help, so the curve is not solved beyond it (and the solve
        is roughly quadratic in the target, so this is the cheapest lever there is).
    prefilter: how many first-roll-EV leaders enter the turn_ev sieve. Defaults to
        `first_roll_keep(total)`, which scales with the number of candidate sets so
        that flooding by near-duplicate strong sets cannot bury the optimum.
    shortlist: how many turn_ev leaders get the full need-aware solve. Defaults to
        `refine` -- solve exactly the depth that will be displayed, so "top 10" costs
        ten solves, not a hidden surplus. The turn_ev sieve is a near-perfect proxy
        for g (it pulls the g-optimum to the front), so no cushion beyond the shown
        depth is needed; callers that want extra margin (the audit) pass it in.
    """
    if max_target is None:
        max_target = target
    if shortlist is None:
        shortlist = refine
    # Report every standard wager up to the cap, plus the requested target itself
    # (which lets a caller rank for a non-standard, cheaper wager -- handy in tests).
    within = {t for t in NEED_TARGETS if t <= max_target}
    if target <= max_target:
        within.add(target)
    targets = tuple(sorted(within)) or (min(NEED_TARGETS),)
    max_target = max(targets)           # never solve past the furthest reported target
    if target not in targets:           # only reported targets can be the sort key
        target = max_target
    pool_names, pool = [], []
    for name, count in sorted(inventory.items()):
        if name == fill_die or count <= 0:
            continue
        pool_names.append(name)
        pool.append((dice_lookup[name], min(int(count), 6)))

    if fill_die:
        pool_names.append(fill_die)
        pool.append((dice_lookup[fill_die], 6))

    if not pool:
        return {"results": [], "combinations_ranked": 0, "rank_seconds": 0.0,
                "solve_seconds": 0.0, "filtered": 0, "solved": 0}

    def note(stage, done, total):
        if progress:
            progress(stage, done, total)

    t0 = time.time()
    note("ranking", 0, 1)
    ranked = rank_combinations(pool, lambda d, t: note("ranking", d, t))

    t1 = time.time()
    if prefilter is None:
        prefilter = first_roll_keep(len(ranked))
    n_full = min(len(ranked), max(refine, shortlist))
    n_tev = min(len(ranked), max(prefilter, n_full))

    def expand(counts):
        names, rows = [], []
        for i, c in enumerate(counts):
            names.extend([pool_names[i]] * c)
            rows.extend([pool[i][0]] * c)
        return names, rows

    # Stage 2: turn_ev sieve over the first-roll leaders, to a horizon that reaches
    # the ranking target (so it does not under-rank big-turn sets -- see sieve_nt).
    # The horizon is target-dependent, so the tev cache is keyed per target.
    snt = sieve_nt(max_target)
    meta = []   # per stage-1 slot: (sorted-name key, names, rows, counts)
    tev_entries = []
    for k, (ev1, counts) in enumerate(ranked[:n_tev]):
        names, rows = expand(counts)
        key = tuple(sorted(names))
        meta.append((key, names, rows, counts))
        tev_entries.append((k, key, (k, rows, snt)))
    tev = _solve_stage("tev", _turn_ev_one, tev_entries, "filtering", note, n_tev,
                       max_target=max_target)

    # Stage 3: full need-aware solve of the turn_ev leaders.
    keep = sorted(range(len(meta)), key=lambda k: -tev[k])[:n_full]
    full_entries = [(k, meta[k][0], (k, meta[k][2], meta[k][1], max_target))
                    for k in keep]
    solved = _solve_stage("full", _solve_one, full_entries, "solving", note,
                          n_full, max_target=max_target)

    out = []
    for k in keep:
        _, names, _, counts = meta[k]
        res = solved[k]
        td = res["turn_dist"]
        g = res["need"]["g"]
        card = res["card"]
        out.append({
            "dice": names,
            "counts": [[pool_names[i], c] for i, c in enumerate(counts) if c],
            # Need-aware expected turns at every standard wager: the ranking metric.
            "g": {t: round(g[t // STEP], 3) for t in targets},
            # --- Card figures, all under the need-aware policy at each wager -------
            # P(the whole wager banks this turn); the aggression far from the target
            # and its collapse in the endgame; per-hand-size throw risk; and whether
            # the policy ever banks short of the need (the strategy exceptions).
            "win1": {t: round(card["win1"][t], 4)
                     for t in targets if t in card["win1"]},
            "bust_far": {t: round(card["bust_far"][t], 4)
                         for t in targets if t in card["bust_far"]},
            "bust_end": round(card["bust_end"], 4),
            "roll_risk": {t: {n: (round(v, 4) if v is not None else None)
                              for n, v in card["roll_risk"][t].items()}
                          for t in targets if t in card["roll_risk"]},
            "strategy": card["strategy"],
            # --- Diagnostics (points-max policy); not shown on the card -----------
            "turn_ev": round(res["turn_ev"], 1),
            "turns_to_win": {t: round(v, 2) for t, v in res["turns_to_win"].items()},
            "turn_dist": {
                "bust": round(td["bust"], 4),
                "p25": td["p25"], "median": td["median"], "p75": td["p75"],
                "cond_mean": (round(td["cond_mean"], 1)
                              if td["cond_mean"] is not None else None),
            },
            "bank_at": res["bank_at"],
            "shape": res["shape"],
        })
    # Fewest need-aware turns to the chosen wager is the ranking that matters at
    # the table; turn_ev and the legacy renewal ride along as diagnostics. Only the
    # top `refine` are shown, but more were solved so the true best is not missed.
    out.sort(key=lambda r: r["g"][target])
    out = out[:refine]

    return {
        "results": out,
        "combinations_ranked": len(ranked),
        "filtered": n_tev,
        "solved": n_full,
        "rank_seconds": round(t1 - t0, 2),
        "solve_seconds": round(time.time() - t1, 2),
    }

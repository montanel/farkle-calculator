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

from .rules import FULL_HAND, best_take, build_move_table, outcome_probabilities

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


def value_table(dice_probs, work=None):
    """Solve V(hand, t) for six given dice and return the flat value array.

    `work` accepts an already-built `_work_lists` result so a caller that also
    runs the forward pass does not build it twice.

    V(hand, t) = max( bank t , expected value of rolling `hand` )

    Solved by backward induction on t. Every legal set-aside scores at least 50,
    so any reroll strictly increases t; sweeping t downwards therefore means
    every value the recursion asks for is already final. No iteration to
    convergence, no approximation -- one pass gives the exact answer.

    Index as V[hand * ROW + t // STEP].
    """
    # Flat value array: V[hand * ROW + t_index]. Flat because the inner loop
    # runs tens of millions of times and one list index beats two.
    V = [0.0] * (64 * ROW)
    for hand in range(64):
        base = hand * ROW
        for ti in range(NT, ROW):
            V[base + ti] = ti * STEP  # past the cap: banking is forced

    # Per-hand work lists: each entry pairs a move-set with the total
    # probability of landing in a roll that offers it. Zero-probability buckets
    # are dropped, which is a large saving for dice with impossible faces.
    single, multi, _ = work if work is not None else _work_lists(dice_probs)

    for ti in range(NT - 1, -1, -1):
        t = ti * STEP
        for hand in range(1, 64):
            acc = 0.0
            for p, off in single[hand]:
                acc += p * V[off + ti]
            for p, offs in multi[hand]:
                acc += p * max([V[o + ti] for o in offs])
            V[hand * ROW + ti] = acc if acc > t else t

    return V


def turn_ev(dice_probs, want_policy=False, names=None):
    """Expected points banked in one turn with six given dice, under optimal play.

    `names` labels each die so the policy can say which hand a threshold is for.
    """
    work = _work_lists(dice_probs) if want_policy else None
    V = value_table(dice_probs, work)
    result = {"turn_ev": V[FULL_HAND * ROW]}
    if want_policy:
        result["bank_at"] = _bank_thresholds(V, names)
        result["shape"] = _turn_shape(V, dice_probs, work=work)
        result["turns_to_win"] = turns_to_win(V, work)
        result["turn_dist"] = turn_distribution(V, work)
    return result


# --- Play-along advisor -------------------------------------------------------
# The tables above summarise the policy; this answers the question the player
# actually faces mid-game: "these dice just landed -- what do I set aside, and
# do I throw again or pass?" It reads the same solved V, so the advice IS the
# optimal policy, not a paraphrase of it.

_ADVICE_CACHE = {}


def prepare(rows):
    """(V, work) for one set of six dice, cached. ~1.2s cold, instant after."""
    key = tuple(tuple(r) for r in rows)
    hit = _ADVICE_CACHE.get(key)
    if hit is None:
        if len(_ADVICE_CACHE) >= 8:
            _ADVICE_CACHE.pop(next(iter(_ADVICE_CACHE)))
        work = _work_lists(rows)
        hit = (value_table(rows, work), work)
        _ADVICE_CACHE[key] = hit
    return hit


def _roll_value(V, work, hand, ti):
    """Expected final banked score if `hand` is thrown at turn score ti*STEP.

    One state's worth of the value_table inner loop. V[hand] itself is
    max(bank, roll); this is the roll side alone, needed to show what throwing
    would have been worth even when passing is the right call.
    """
    single, multi, _ = work
    acc = 0.0
    for p, off in single[hand]:
        acc += p * V[off + ti]
    for p, offs in multi[hand]:
        acc += p * max(V[o + ti] for o in offs)
    return acc


def advise(rows, mask, faces, t):
    """The optimal play for one rolled hand.

    rows: the set's six probability rows, in a fixed order.
    mask: which of the six dice were just thrown (bit i = die i).
    faces: {die index: face 1..6} for every die in `mask`.
    t: points set aside earlier this turn (the round tally), multiple of STEP.

    Chooses the set-aside maximising V(remaining hand, t + points) -- the same
    argmax the backward pass scored -- then reads the throw/pass decision off
    the next state. Ties go to the larger set-aside, which banks more if the
    turn ends here.
    """
    V, work = prepare(rows)
    mem = [i for i in range(6) if mask >> i & 1]
    oid = sum((faces[i] - 1) * 6 ** j for j, i in enumerate(mem))
    options = moves()[mask][oid]
    if not options:
        return {"bust": True, "lost": t}

    ti = t // STEP
    _, pts, next_mask = max(
        (V[nm * ROW + ti + p // STEP], p, nm) for nm, p in options)

    # next_mask == FULL_HAND means the throw was fully scored (hot dice); the
    # kept dice are then the whole throw, not mask & ~FULL_HAND.
    kept = mask if next_mask == FULL_HAND else mask & ~next_mask
    t2 = t + pts
    ti2 = t2 // STEP
    forced = ti2 >= NT  # past the cap the tail forces banking
    roll_val = None if forced else _roll_value(V, work, next_mask, ti2)
    bank = forced or roll_val <= t2 + 1e-9
    return {
        "bust": False,
        "keep": [i for i in range(6) if kept >> i & 1],
        "points": pts,
        "turn_score": t2,
        "action": "bank" if bank else "reroll",
        "next_mask": next_mask,
        "reroll_count": bin(next_mask).count("1"),
        "hot": kept == mask,
        "roll_value": None if roll_val is None else round(roll_val, 1),
    }


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
NEED_TARGETS = (1500, 2000, 2500, 3000, 4000)
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
                us = [U[o + ti] for o in offs]
                mu = min(us)           # need-aware: fewest expected turns, not most points
                ru += p * mu
                rb += p * B[offs[us.index(mu)] + ti]
            k = hand * ROW + ti
            if gbank is not None and gbank <= ru:   # banking is (weakly) better: stop
                U[k] = gbank
                B[k] = 0.0
            else:
                U[k] = ru
                B[k] = rb
    fb = FULL_HAND * ROW
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

    Each set is independent, so the exact solve parallelises perfectly.
    """
    index, rows, names = job
    res = turn_ev(rows, want_policy=True, names=names)
    return index, res


_POOL = None
_POOL_LOCK = threading.Lock()


def default_workers():
    return max(1, min(8, os.cpu_count() or 1))


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


def _solve_serial(jobs, note):
    out = []
    for k, job in enumerate(jobs):
        note("solving", k, len(jobs))
        out.append(_solve_one(job))
    return out


def _solve_many(jobs, note, workers=None):
    """Solve every set, in parallel when there is enough work to pay for it.

    Falls back to a plain loop if the pool cannot start -- a sandbox with no
    process spawning should give slower answers, not no answers.
    """
    total = len(jobs)
    if total <= 2 or (workers is not None and workers == 1):
        return _solve_serial(jobs, note)

    try:
        pool = shared_pool(workers)
        note("starting", 0, total)
        # submit + as_completed rather than map: map yields in submission order,
        # so one slow set holds back the count for every set finished behind it.
        futures = [pool.submit(_solve_one, job) for job in jobs]
        out, done = [], 0
        for fut in as_completed(futures):
            out.append(fut.result())
            done += 1
            note("solving", done, total)
        return out
    except (OSError, RuntimeError, ImportError, BrokenProcessPool):
        _discard_pool()
        return _solve_serial(jobs, note)


def _hand_threshold(V, hand, tol=1e-9):
    """Lowest turn score at which banking beats rolling this exact hand.

    None means rolling stays worthwhile all the way to the bank cap.
    """
    base = hand * ROW
    for ti in range(NT):
        if V[base + ti] <= ti * STEP + tol:  # banking is (weakly) optimal
            return ti * STEP
    return None


def _bank_thresholds(V, names=None):
    """When to stop, both summarised and hand by hand.

    The threshold depends on *which* dice are left, not just how many, so this
    reports two views:

      by_size  {n: {"safe": x, "high": y}} -- `safe` is the lowest threshold
               across every hand of that size, so following it never rolls past
               the optimal point for the hand you actually hold. `high` is the
               largest. Either may be null, meaning rolling on is always right.

      hands    one entry per specific hand, naming the dice, for when you want
               the exact number rather than the safe one.
    """
    by_size, seen, hands = {}, set(), []
    for hand in range(1, 64):
        n = bin(hand).count("1")
        thresh = _hand_threshold(V, hand)
        by_size.setdefault(n, []).append(thresh)
        if not names:
            continue
        # Two copies of the same die are interchangeable, so hands that differ
        # only in which copy is held are one row as far as the player is concerned.
        held = sorted(names[i] for i in range(6) if hand >> i & 1)
        key = tuple(held)
        if key in seen:
            continue
        seen.add(key)
        hands.append({"n": n, "dice": held, "bank": thresh})

    summary = {}
    for n, vals in by_size.items():
        known = [v for v in vals if v is not None]
        summary[n] = {
            "safe": min(known) if known else None,
            "high": (max(known) if known and len(known) == len(vals) else None),
        }

    hands.sort(key=lambda h: (-h["n"], h["bank"] is None, h["bank"] or 0))
    return {"by_size": summary, "hands": hands}


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

def optimize(inventory, dice_lookup, refine=20, progress=None, fill_die=None):
    """Rank every legal 6-dice selection, then solve the best `refine` exactly.

    inventory: {die name: how many you own}.
    dice_lookup: name -> [p1..p6], probabilities summing to 1.
    fill_die: the die the game puts in any slot you do not fill yourself. It is
        always available up to a full hand regardless of how many you own, so
        selections using fewer than six special dice are considered too.
    """
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
        return {"results": [], "combinations_ranked": 0,
                "rank_seconds": 0.0, "solve_seconds": 0.0}

    def note(stage, done, total):
        if progress:
            progress(stage, done, total)

    t0 = time.time()
    note("ranking", 0, 1)
    ranked = rank_combinations(pool, lambda d, t: note("ranking", d, t))

    t1 = time.time()
    todo = ranked[:refine]

    jobs = []
    for k, (ev1, counts) in enumerate(todo):
        names, rows = [], []
        for i, c in enumerate(counts):
            names.extend([pool_names[i]] * c)
            rows.extend([pool[i][0]] * c)
        jobs.append((k, rows, names))

    solved = dict(_solve_many(jobs, note))

    out = []
    for k, (ev1, counts) in enumerate(todo):
        res = solved[k]
        td = res["turn_dist"]
        out.append({
            "dice": jobs[k][2],
            "counts": [[pool_names[i], c] for i, c in enumerate(counts) if c],
            "turn_ev": round(res["turn_ev"], 1),
            "turns_to_win": {t: round(v, 2) for t, v in res["turns_to_win"].items()},
            # The mean (turn_ev) alone flatters an aggressive set; ship the shape
            # behind it so the UI can show the skew rather than hide it.
            "turn_dist": {
                "bust": round(td["bust"], 4),
                "p25": td["p25"], "median": td["median"], "p75": td["p75"],
                "cond_mean": (round(td["cond_mean"], 1)
                              if td["cond_mean"] is not None else None),
            },
            "bank_at": res["bank_at"],
            "shape": res["shape"],
        })
    # Fewer turns to reach the default wager is the ranking that matters at the
    # table; turn_ev rides along as a secondary figure. Stage 1 still shortlists
    # by first-roll EV, a fine proxy for which sets are worth solving exactly.
    out.sort(key=lambda r: r["turns_to_win"][DEFAULT_TARGET])

    return {
        "results": out,
        "combinations_ranked": len(ranked),
        "rank_seconds": round(t1 - t0, 2),
        "solve_seconds": round(time.time() - t1, 2),
    }

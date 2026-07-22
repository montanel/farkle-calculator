"""Tests for the Farkle rules and solver.

    python tests.py          # fast checks (~15s)
    python tests.py --slow   # also Monte-Carlo validates the DP (~2min)
"""

import math
import pathlib
import random
import sys

from farkle.rules import (FULL_HAND, best_score, best_take, members,
                          get_all_possible_combinations, outcome_probabilities)
from farkle import solver
from farkle.dice import (DICE, DICE_PROBS, DICE_WEIGHTS, DICE_ICON, WILDCARD,
                         DEFAULT_DIE, DICE_WIKI, WIKI_BASE, _WIKI_TITLE)

WEB_IMG = pathlib.Path(__file__).parent / "farkle" / "web" / "img"

# No real die is deterministic, so build one to exercise the degenerate paths.
ALWAYS_THREE = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]

FAILED = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<44} {got}")


def close(name, got, want, tol):
    ok = abs(got - want) <= tol
    if not ok:
        FAILED.append(f"{name}: got {got}, want {want} +/- {tol}")
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<44} {got:.2f} (want {want} +/-{tol})")


def cv(*faces):
    c = [0] * 6
    for f in faces:
        c[f - 1] += 1
    return tuple(c)


def test_scoring():
    print("\nScoring rules (KCD2 wiki table)")
    check("single 1", best_score(cv(1)), 100)
    check("single 5", best_score(cv(5)), 50)
    check("1 + 5", best_score(cv(1, 5)), 150)
    check("three 1s", best_score(cv(1, 1, 1)), 1000)
    check("three 2s", best_score(cv(2, 2, 2)), 200)
    check("three 6s", best_score(cv(6, 6, 6)), 600)
    check("four 4s (doubles)", best_score(cv(4, 4, 4, 4)), 800)
    check("five 4s (doubles again)", best_score(cv(4, 4, 4, 4, 4)), 1600)
    check("six 4s", best_score(cv(4, 4, 4, 4, 4, 4)), 3200)
    check("six 1s", best_score(cv(1, 1, 1, 1, 1, 1)), 8000)
    check("partial straight 1-5", best_score(cv(1, 2, 3, 4, 5)), 500)
    check("partial straight 2-6", best_score(cv(2, 3, 4, 5, 6)), 750)
    check("full straight", best_score(cv(1, 2, 3, 4, 5, 6)), 1500)

    print("\n  decomposition must beat greedy")
    check("straight + spare 1", best_score(cv(1, 1, 2, 3, 4, 5)), 600)
    check("straight + spare 5", best_score(cv(1, 2, 3, 4, 5, 5)), 550)
    check("2-6 straight beats 1-5+extra", best_score(cv(2, 3, 4, 5, 6)), 750)

    print("\n  illegal set-asides return None")
    check("lone 3", best_score(cv(3)), None)
    check("pair of 3s", best_score(cv(3, 3)), None)
    check("1 with a dangling 2", best_score(cv(1, 2)), None)

    print("\n  best_take may leave dice behind")
    check("take the 1, drop the 2", best_take(cv(1, 2)), 100)
    check("nothing scores", best_take(cv(2, 2, 3, 3, 4, 6)), 0)
    check("take triple, drop rest", best_take(cv(6, 6, 6, 2, 3, 4)), 600)


def test_selection_enumeration():
    print("\nSelection enumeration")
    check("Farkle roll has no moves",
          len(get_all_possible_combinations([2, 2, 3, 3, 4, 6])), 0)
    check("1-6 straight roll", len(get_all_possible_combinations([1, 2, 3, 4, 5, 6])), 6)
    check("all six 1s -> every subset legal",
          len(get_all_possible_combinations([1] * 6)), 63)
    opts = get_all_possible_combinations([5, 5, 5, 5, 2, 3])
    check("four 5s is the best move there", max(p for _, p in opts), 1000)
    for idx, pts in get_all_possible_combinations([1, 2, 3, 4, 5, 5]):
        counts = [0] * 6
        for i in idx:
            counts[[1, 2, 3, 4, 5, 5][i] - 1] += 1
        if best_score(tuple(counts)) != pts:
            FAILED.append("enumerated points disagree with best_score")
    print("  ok   enumerated points agree with best_score")


def test_dice_data():
    print("\nDice data")
    bad = [n for n, p in DICE_PROBS.items() if abs(sum(p) - 1) > 1e-12]
    check("every distribution sums to 1", bad, [])
    check("optimisable dice", len(DICE_WEIGHTS), 41)
    check("wildcard dice excluded from play", sorted(WILDCARD),
          ["Balatro's die", "Devil's head die"])
    check("wildcards absent from the solver pool",
          [n for n in WILDCARD if n in DICE_PROBS], [])
    check("Pie die can never roll 5 or 6",
          [DICE_PROBS["Pie die"][4], DICE_PROBS["Pie die"][5]], [0.0, 0.0])
    # The original JS data claimed this die always rolls a 3; the wiki says
    # otherwise, and getting it wrong badly overrates the die.
    check("Saint Antiochus' die matches the wiki (40% threes)",
          DICE_WEIGHTS["Saint Antiochus' die"], ([3, 1, 6, 1, 1, 3], 15))
    check("every die has an icon on disk",
          [n for n, i in DICE_ICON.items()
           if not i or not (WEB_IMG / i).is_file()], [])
    # Names are scraped from wikitext; a greedy regex once swallowed a whole
    # inline infobox into one of them, which silently broke the wildcard lookup.
    check("names are clean (no leftover wiki markup)",
          [n for n in DICE if "|" in n or "=" in n or "}" in n or len(n) > 40], [])
    check("every wildcard name exists in the dice table",
          [n for n in WILDCARD if n not in DICE], [])

    # Wiki links are built from the die name, so a name change silently produces
    # a dead link. These check the construction, not the network: the URLs were
    # verified once against action=query&prop=info&inprop=url for all 43 pages.
    check("every die has a wiki link", sorted(set(DICE) - set(DICE_WIKI)), [])
    check("wiki links are absolute and on the wiki",
          [n for n, u in DICE_WIKI.items() if not u.startswith(WIKI_BASE)], [])
    # A raw apostrophe or space in a href is the exact bug this encoding avoids.
    check("wiki links are properly encoded",
          [n for n, u in DICE_WIKI.items()
           if " " in u or "'" in u or '"' in u], [])
    # Disambiguated pages exist for real dice only; a stale entry would point a
    # renamed die at a page that no longer matches it.
    check("every disambiguated title names a real die",
          [n for n in _WIKI_TITLE if n not in DICE], [])
    check("apostrophes survive as %27",
          DICE_WIKI["King's die"], WIKI_BASE + "King%27s_die")
    check("disambiguation suffix is kept",
          DICE_WIKI["Ci die"], WIKI_BASE + "Ci_die/KCD2")


def test_probability_tables():
    print("\nProbability bookkeeping")
    for name in ["Ordinary die", "King's die", "Pie die"]:
        dp = [DICE_PROBS[name]] * 6
        for hand in (0b1, 0b101, FULL_HAND):
            total = sum(outcome_probabilities(hand, dp))
            if abs(total - 1) > 1e-9:
                FAILED.append(f"outcome probs for hand {hand} sum to {total}")
    print("  ok   outcome distributions sum to 1")

    # Outcome ids must line up between the probability vector and the move table.
    mv = solver.moves()
    dp = [ALWAYS_THREE] * 6  # deterministic: always all 3s
    probs = outcome_probabilities(FULL_HAND, dp)
    live = [i for i, p in enumerate(probs) if p > 0]
    check("deterministic dice -> exactly one outcome", len(live), 1)
    check("that outcome is six 3s worth 2400",
          max(pts for _, pts in mv[FULL_HAND][live[0]]), 2400)


def test_solver_values():
    print("\nSolver")
    fair = [DICE_PROBS["Ordinary die"]] * 6
    close("fair dice, first roll", solver.first_roll_ev(fair), 399.1, 0.2)
    close("fair dice, optimal turn", solver.turn_ev(fair)["turn_ev"], 564.4, 0.2)

    # Six dice that always roll 3s: every roll is six 3s = 2400, never a Farkle,
    # so the solver should roll forever and stop only at the bank cap.
    ant = [ALWAYS_THREE] * 6
    check("deterministic dice, first roll", solver.first_roll_ev(ant), 2400.0)
    check("deterministic dice, turn (rolls to the cap)",
          solver.turn_ev(ant)["turn_ev"], 12000.0)

    fair_names = ["Ordinary die"] * 6
    th = solver.turn_ev(fair, want_policy=True, names=fair_names)["bank_at"]
    size = th["by_size"]
    # With identical dice every hand of a given size must agree exactly...
    ok = all(v["safe"] == v["high"] for v in size.values() if v["safe"] is not None)
    if not ok:
        FAILED.append(f"identical dice gave a spread within a hand size: {size}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'identical dice -> no spread within a hand size':<44} ok")
    # ...and identical dice collapse to one named hand per size.
    check("identical dice -> one distinct hand per size",
          sorted({h["n"] for h in th["hands"]}), [1, 2, 3, 4, 5, 6])
    check("duplicate hands collapsed", len(th["hands"]), 6)

    # ...and from two dice up, holding more justifies pushing your luck longer.
    lo = {n: v["safe"] for n, v in size.items()}
    ok = lo[5] > lo[4] > lo[3] > lo[2] and lo[6] is None
    if not ok:
        FAILED.append(f"bank thresholds should rise from 2 dice up: {size}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'2+ dice: more in hand -> roll longer':<44} {lo}")

    # The safe figure must never exceed any real hand of that size, or acting on
    # it would roll past the right moment for the hand actually held.
    bad = [h for h in th["hands"] if h["bank"] is not None
           and h["bank"] < (lo[h["n"]] or 0)]
    check("safe threshold never overshoots a real hand", bad, [])

    # The one-die threshold deliberately breaks that pattern. Rolling your last
    # die is the only way to trigger hot dice, so success refills the hand and
    # the gamble is worth more than the safer-looking two-die roll. Verified by
    # rebuilding the table with hot dice removed, which restores monotonicity.
    ok = lo[1] > lo[2]
    if not ok:
        FAILED.append(f"expected the hot-dice anomaly at one die: {lo}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'hot dice make 1 die worth more than 2':<44} "
          f"{lo[1]} > {lo[2]}")

    ev1 = solver.first_roll_ev(fair)
    ev2 = solver.turn_ev(fair)["turn_ev"]
    ok = ev2 > ev1
    if not ok:
        FAILED.append("playing the turn out should beat stopping after one roll")
    print(f"  {'ok  ' if ok else 'FAIL'} {'optimal play beats one-and-done':<44} "
          f"{ev1:.0f} -> {ev2:.0f}")

    test_turn_shape(fair)


def test_turn_shape(fair):
    """The forward pass: how a turn actually ends, not just where it would stop."""
    res = solver.turn_ev(fair, want_policy=True, names=["Ordinary die"] * 6)
    shape, by_size = res["shape"], res["shape"]["by_size"]

    # _turn_shape asserts this internally; check it here too so a regression
    # reports as a named failure rather than an AssertionError mid-run.
    total = shape["bust"] + sum(s["bank"] for s in by_size.values())
    ok = abs(total - 1.0) < 1e-9
    if not ok:
        FAILED.append(f"turn outcomes should sum to 1, got {total}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'every turn ends somewhere':<44} {total:.12f}")

    # Six fair dice bust ~19% of turns under optimal play -- well below the 2.3%
    # chance of an immediate six-die Farkle plus the risk taken on later rolls.
    ok = 0.15 < shape["bust"] < 0.25
    if not ok:
        FAILED.append(f"fair-dice bust rate out of range: {shape['bust']}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'fair dice bust ~19% of turns':<44} "
          f"{shape['bust'] * 100:.1f}%")

    # Fair dice never *choose* to stop on a full hand, so any six-die banking is
    # forced: hot dice refilled the hand and carried the score past the cap.
    # That is a real path, just a vanishingly rare one -- not the zero it looks
    # like at a glance.
    ok = res["bank_at"]["by_size"][6]["safe"] is None and 0 < by_size[6]["bank"] < 1e-5
    if not ok:
        FAILED.append(f"six-die banking should be forced-only and rare: {by_size[6]}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'six-die banking only via the cap':<44} "
          f"{by_size[6]['bank']:.2e}")

    # The point behind the UI's "how often" column: a high threshold is a rule
    # you will rarely meet. Five dice left is reachable only via hot dice, so
    # almost no turn ends there, while most end at one or two dice.
    ends = {n: by_size[n]["bank"] + by_size[n]["bust"] for n in range(1, 7)}
    ok = ends[5] < 0.06 and ends[1] + ends[2] > 0.5
    if not ok:
        FAILED.append(f"turn endings concentrate at small hands, got {ends}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'turns end with few dice in hand':<44} "
          f"{ {n: round(p * 100, 1) for n, p in ends.items()} }")

    # A single fair die scores on a 1 or a 5, so rolling one busts exactly 4/6 of
    # the time regardless of score. This is the number people expect the table to
    # show, and it is nowhere near the share of turns that *end* on one die --
    # those are almost all banks. Keeping both pinned stops the two being
    # conflated again.
    risk = by_size[1]["bust_if_roll"]
    ok = abs(risk - 2 / 3) < 1e-9
    if not ok:
        FAILED.append(f"rolling one fair die should bust 2/3 of the time, got {risk}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'rolling one fair die busts 2/3':<44} {risk:.4f}")

    ok = by_size[1]["bust"] < 0.01 < 0.2 < by_size[1]["bank"]
    if not ok:
        FAILED.append(f"one-die endings should be overwhelmingly banks: {by_size[1]}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'yet turns ending on one die are banks':<44} "
          f"{by_size[1]['bank'] * 100:.1f}% bank vs {by_size[1]['bust'] * 100:.2f}% bust")

    # Fewer dice is strictly more dangerous to roll: no hand can score with dice
    # it does not have.
    risks = [by_size[n]["bust_if_roll"] for n in range(6, 0, -1)]
    ok = all(a < b for a, b in zip(risks, risks[1:]))
    if not ok:
        FAILED.append(f"bust risk should rise as dice are removed: {risks}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'bust risk climbs as dice run out':<44} "
          f"{[round(x * 100) for x in risks]}")

    # A rarely-reached hand can still carry a high threshold; that combination
    # is exactly what confuses people reading the table, so pin it down.
    five = res["bank_at"]["by_size"][5]["safe"]
    ok = five is not None and five > 2000 and ends[5] < 0.06
    if not ok:
        FAILED.append(f"expected a high but rarely-met five-die threshold: {five}, {ends[5]}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'high threshold != often reached':<44} "
          f"{five} but only {ends[5] * 100:.1f}% of turns")


def test_turns_to_win():
    print("\nTurns to win (renewal over the banked distribution)")
    fair = [DICE_PROBS["Ordinary die"]] * 6
    res = solver.turn_ev(fair, want_policy=True, names=["Ordinary die"] * 6)
    ttw = res["turns_to_win"]

    check("all wager targets reported", sorted(ttw), list(solver.WIN_TARGETS))
    # A harder target can only cost more turns, never fewer.
    vals = [ttw[t] for t in sorted(ttw)]
    ok = all(a < b for a, b in zip(vals, vals[1:]))
    if not ok:
        FAILED.append(f"turns to win should rise with the target: {ttw}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'more points needed -> more turns':<44} "
          f"{ {t: round(v, 2) for t, v in ttw.items()} }")

    # Sanity floor: fair dice bank ~564 a turn and bust ~19%, so reaching 4000
    # takes well more than 4000/564 = 7.1 turns once wasted busts are counted.
    ok = 7.1 < ttw[4000] < 9.0
    if not ok:
        FAILED.append(f"fair-dice turns-to-4000 out of range: {ttw[4000]}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'fair dice reach 4000 in ~8 turns':<44} {ttw[4000]:.2f}")

    # A busted turn banks nothing, so turns-to-win must exceed the frictionless
    # target/turn_ev estimate that pretends every turn lands its average.
    naive = 4000 / res["turn_ev"]
    ok = ttw[4000] > naive
    if not ok:
        FAILED.append(f"turns-to-win {ttw[4000]} should beat naive {naive}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'bust friction shows vs target/turn_ev':<44} "
          f"{ttw[4000]:.2f} > {naive:.2f}")

    # The deterministic 3s die never busts and banks 2400 a roll to the 12000
    # cap, so a 4000 game is one turn flat -- the renewal's no-friction floor.
    ant = [ALWAYS_THREE] * 6
    at = solver.turn_ev(ant, want_policy=True, names=["Ordinary die"] * 6)["turns_to_win"]
    check("never-bust dice win 4000 in one turn", round(at[4000], 6), 1.0)


def test_need_curve():
    """The need-aware solver: expected turns to clear a remaining need r.

    Checks the three Task-2 invariants (non-decreasing, the g[STEP] identity, and
    g <= the legacy EV-max renewal at every r), that the bust fixed point
    converges well inside its cap for a spread of dice, and a fast regression pin.
    The exact g[2000] pin lives in the --slow run since solving that far costs a
    few seconds.
    """
    print("\nNeed-aware turns-to-clear (need_curve)")
    STEP = solver.STEP
    fair = [DICE_PROBS["Ordinary die"]] * 6
    fwork = solver._work_lists(fair)
    farkle6 = fwork[2][FULL_HAND]

    # Invariant: g[STEP] = 1 / P(a turn banks at least 50). For r = 50 the optimal
    # policy is "bank the first thing that scores", so the turn wins exactly when
    # the six-die opening roll is not a farkle -- checkable nearly by hand.
    g50 = solver.need_curve(fair, max_target=STEP, work=fwork)["g"][1]
    close("g[STEP] = 1/(1-farkle6)", g50, 1.0 / (1.0 - farkle6), 1e-9)

    # Convergence and the other two invariants across a spread: ordinary, extreme-
    # 1s Weighted (strong), a straight-oriented mix, a die with impossible faces
    # (Pie has no 5s or 6s, exercising the zero-probability pruning under the min
    # objective), and a never-bust deterministic die (bust prob 0 -- the 1/(1-b)
    # edge). max_target is small so the whole spread stays quick.
    always_three = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    spread = {
        "all-ordinary": fair,
        "six Weighted": [DICE_PROBS["Weighted die"]] * 6,
        "straight-mix": [DICE_PROBS[n] for n in
                         ["King's die", "Odd die", "Lucky die",
                          "Grimy die", "Ordinary die", "Ordinary die"]],
        "impossible-face Pie": [DICE_PROBS["Pie die"]] * 6,
        "deterministic 3s": [always_three] * 6,
    }
    # Small target keeps the spread quick on a slow interpreter; the invariants
    # hold at every r regardless of how far the curve runs. The g <= legacy check
    # needs a value_table per set, so only a representative few pay for it (weak
    # ordinary, strong Weighted, pruned Pie); convergence is checked on all.
    tgt = 300
    legacy_check = {"all-ordinary", "six Weighted", "impossible-face Pie"}
    gmap, bustmap, bad_dec, bad_legacy, worst_iter = {}, {}, [], [], 0
    for name, dp in spread.items():
        work = solver._work_lists(dp)
        # need_curve asserts non-decreasing internally and raises if the fixed
        # point overruns its cap, so reaching here already proves convergence.
        res = solver.need_curve(dp, max_target=tgt, work=work)
        g, iters = res["g"], res["iters"]
        gmap[name], bustmap[name] = g, res["bust"]
        worst_iter = max(worst_iter, max(iters))
        if not all(g[i] <= g[i + 1] + 1e-9 for i in range(len(g) - 1)):
            bad_dec.append(name)
        # The need-aware policy is optimal for exactly this objective, so it can
        # never take more turns than the EV-max renewal -- at every r, not just at
        # the endpoint.
        if name in legacy_check:
            V = solver.value_table(dp, work)
            legacy = solver.turns_to_win(
                V, work, targets=tuple(range(STEP, tgt + 1, STEP)))
            if any(g[r // STEP] > legacy[r] + 1e-9
                   for r in range(STEP, tgt + 1, STEP)):
                bad_legacy.append(name)

    check("g non-decreasing for every set", bad_dec, [])
    check("need-aware g <= legacy turns_to_win", bad_legacy, [])
    ok = 0 < worst_iter < solver.NEED_ITER_CAP
    if not ok:
        FAILED.append(f"fixed point iterations out of range: {worst_iter}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'fixed point converges inside the cap':<44} "
          f"max {worst_iter} of {solver.NEED_ITER_CAP}")

    # Endgame sanity at r = 300 (index 6). The need-aware policy banks the moment
    # the tally covers the need, so a turn started this close should rarely bust --
    # if any set still lost ~half its turns here, the "bank forced at t >= r" rule
    # or a min/max slip would be the culprit. Every set stays well under half.
    worst_bust300 = max(bustmap[n][6] for n in spread)
    ok = worst_bust300 < 0.30
    if not ok:
        FAILED.append(f"r=300 bust fraction too high somewhere: {worst_bust300}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'r=300 turns rarely bust (not ~50%)':<44} "
          f"worst {worst_bust300 * 100:.1f}%")

    # A strong set all but clears 300 in a single turn: g[300] barely above 1 and
    # a bust fraction near zero.
    wg300, wbust300 = gmap["six Weighted"][6], bustmap["six Weighted"][6]
    ok = wg300 < 1.02 and wbust300 < 0.01
    if not ok:
        FAILED.append(f"strong set endgame off: g[300]={wg300}, bust={wbust300}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'strong set clears r=300 in ~1 turn':<44} "
          f"g[300]={wg300:.4f}, bust {wbust300 * 100:.2f}%")

    # The banking boundary itself: at any t >= r the solved table must read U = 1
    # (bank now and win), for every hand -- the rule that makes the endgame work.
    _, _, _, U = solver.solve_need(fwork, gmap["all-ordinary"], 6,
                                   gmap["all-ordinary"][6])
    ROW = solver.ROW
    boundary_ok = all(U[h * ROW + ti] == 1.0
                      for h in range(1, 64) for ti in range(6, 10))
    check("t >= r banks and wins (U == 1)", boundary_ok, True)

    # Strong dice clear a need in fewer turns: six Weighted (extreme 1s) all but
    # guarantee 50 on the opening roll, beating ordinary dice.
    wg, og = gmap["six Weighted"][1], gmap["all-ordinary"][1]
    ok = wg < og
    if not ok:
        FAILED.append(f"Weighted should clear 50 faster than ordinary: {wg} vs {og}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'stronger dice clear a need faster':<44} "
          f"Weighted {wg:.4f} < ordinary {og:.4f}")

    # Regression pin (exact solver, reviewed once): six ordinary dice average this
    # many turns to bank 300. g[2000] is pinned in the --slow run.
    close("g[300] pin (six ordinary dice)", gmap["all-ordinary"][6],
          1.162884550685739, 1e-6)


def test_need_curve_pin():
    """Slow exact pin: the g[2000] value the spec calls for (~15s to solve)."""
    print("\nNeed-aware regression pin (exact g[2000])")
    fair = [DICE_PROBS["Ordinary die"]] * 6
    g2000 = solver.need_curve(fair, max_target=2000)["g"][40]
    close("g[2000] pin (six ordinary dice)", g2000, 4.2657554150, 1e-6)


def test_turn_distribution():
    """The shape behind turn_ev: bust rate, quartiles, conditional mean.

    turn_ev is the *mean* of the per-turn banked score, and for strong sets that
    mean is unrepresentative -- a big bust mass plus a fat hot-dice tail. These
    pins keep the surfaced distribution honest and tied to the same policy.
    """
    print("\nTurn distribution (the skew behind turn_ev)")
    ROW = solver.ROW
    names = ["Ordinary die", "Heavenly Kingdom die",
             "Die of misfortune", "Weighted die"]

    worst_sum = worst_bust = worst_mean = 0.0
    order_bad, median_bad = [], []
    for name in names:
        dp = [DICE_PROBS[name]] * 6
        work = solver._work_lists(dp)
        V = solver.value_table(dp, work)
        td = solver.turn_distribution(V, work)
        banked = solver._banked_distribution(V, work)
        shape = solver._turn_shape(V, dp, work=work)

        worst_sum = max(worst_sum, abs(sum(banked.values()) - 1.0))
        worst_bust = max(worst_bust, abs(td["bust"] - shape["bust"]))
        worst_mean = max(worst_mean, abs(td["mean"] - V[FULL_HAND * ROW]))
        if not td["p25"] <= td["median"] <= td["p75"]:
            order_bad.append((name, td["p25"], td["median"], td["p75"]))
        # A right-skewed turn banks less at the median than it averages once it
        # banks at all; the conditional mean strips out the busts that drag the
        # median down, so it can never sit below the median.
        if td["cond_mean"] is None or td["median"] > td["cond_mean"] + 1e-9:
            median_bad.append((name, td["median"], td["cond_mean"]))

    close("banked probabilities sum to 1", worst_sum, 0.0, 1e-9)
    close("mean equals turn_ev", worst_mean, 0.0, 1e-6)
    close("bust mass matches _turn_shape", worst_bust, 0.0, 1e-9)
    check("quartiles ordered p25 <= median <= p75", order_bad, [])
    check("median <= conditional mean", median_bad, [])

    # The honesty fix in one set: six Heavenly Kingdom dice bust over a third of
    # turns, so a full quarter bank nothing (p25 = 0) and the median turn (1200)
    # is far below both the mean (turn_ev ~1424) and the mean-when-it-banks
    # (~2258). turn_ev alone would badly misrepresent a typical turn.
    dp = [DICE_PROBS["Heavenly Kingdom die"]] * 6
    work = solver._work_lists(dp)
    V = solver.value_table(dp, work)
    td = solver.turn_distribution(V, work)
    check("strong set: a quarter of turns bank nothing", td["p25"], 0)
    ok = td["median"] < V[FULL_HAND * ROW] < td["cond_mean"]
    if not ok:
        FAILED.append(f"expected median < turn_ev < cond_mean, got "
                      f"{td['median']} < {V[FULL_HAND * ROW]:.0f} < {td['cond_mean']:.0f}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'median < turn_ev < mean-when-it-banks':<44} "
          f"{td['median']} < {V[FULL_HAND * ROW]:.0f} < {td['cond_mean']:.0f}")


def test_advisor():
    """The need-aware advisor: the turns-minimising move for one rolled hand.

    Unlike the old points-max advisor, this banks the instant a set-aside covers
    the remaining need (it wins), so its endgame advice is qualitatively new. The
    consistency audit is the guard the whole upgrade exists for: `advise` must
    reproduce the U_r argmin and the bank/roll comparison the solver would make,
    or the advisor and solver have silently drifted apart.
    """
    print("\nPlay-along advisor (need-aware)")
    fair = [DICE_PROBS["Ordinary die"]] * 6
    STEP, ROW = solver.STEP, solver.ROW
    tgt = 800   # small target keeps the shared need curve quick to build

    # Endgame: the need-aware policy banks the moment the tally covers the need.
    # A 5 set aside lifts 250 -> 300 = need, which wins outright: bank, not roll.
    r = solver.advise(fair, 0b1, {0: 5}, 250, need=300, target=tgt)
    check("covering the need wins", (r["won"], r["action"]), (True, "bank"))
    check("winning throw clears for certain", r["clear_prob"], 1.0)
    check("kept the scoring 5", (r["keep"], r["points"]), ([0], 50))

    # A full straight covers the need too (1500 >= 800), so even with hot dice
    # back the play is to bank the win rather than chase points -- the endgame
    # behaviour the points-max advisor never showed.
    r = solver.advise(fair, 63, {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6}, 0,
                      need=tgt, target=tgt)
    check("straight worth 1500", r["points"], 1500)
    check("hot dice, but the need is met", (r["hot"], r["won"]), (True, True))
    check("so bank the win, do not roll on", r["action"], "bank")

    # Far from the target with five dice back, keeping a single 1 and throwing on
    # is fewer expected turns than banking 100 -- the advisor rolls.
    r = solver.advise(fair, 63, {0: 1, 1: 2, 2: 3, 3: 4, 4: 6, 5: 6}, 0,
                      need=tgt, target=tgt)
    check("kept the lone 1", (r["keep"], r["points"]), ([0], 100))
    check("far from target: throw on", r["action"], "reroll")
    ok = r["roll_turns"] < r["bank_turns"] and 0 < r["clear_prob"] < 1
    if not ok:
        FAILED.append(f"throw-on economics off: {r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'rolling costs fewer turns than banking':<44} "
          f"{r['roll_turns']} < {r['bank_turns']}, win {r['clear_prob'] * 100:.1f}%")

    # Nothing scores.
    r = solver.advise(fair, 63, {0: 2, 1: 2, 2: 3, 3: 4, 4: 6, 5: 6}, 300,
                      need=500, target=tgt)
    check("farkle detected, pile lost", (r["bust"], r["lost"]), (True, 300))

    # Consistency audit: at every state, `advise` must pick the U_r argmin set-
    # aside and make the same bank/roll call the solver's own comparison would.
    import random
    rng = random.Random(13)
    ctx = solver.prepare(fair, tgt)
    mv = solver.moves()
    bad, cmin, cmax = [], 2.0, -1.0
    for _ in range(400):
        need = STEP * rng.randrange(1, tgt // STEP + 1)
        ri = need // STEP
        U = ctx.u_table(ri)
        t = STEP * rng.randrange(0, ri)                 # tally below the need
        mask = rng.randrange(1, 64)
        mem = [i for i in range(6) if mask >> i & 1]
        faces = {i: rng.randrange(1, 7) for i in mem}
        r = solver.advise(fair, mask, faces, t, need=need, target=tgt)
        oid = sum((faces[i] - 1) * 6 ** j for j, i in enumerate(mem))
        opts = mv[mask][oid]
        if not opts:
            if not r.get("bust"):
                bad.append((mask, faces, t, need, "missed farkle"))
            continue
        # Independent argmin over set-asides (U = 1 once the tally covers need).
        best = min(U[nm * ROW + (t + p) // STEP] for nm, p in opts)
        got = U[r["next_mask"] * ROW + r["turn_score"] // STEP]
        if abs(got - best) > 1e-9:
            bad.append((mask, faces, t, need, "not the argmin"))
        # Independent bank/roll decision from the same table.
        ti2 = r["turn_score"] // STEP
        if ti2 >= ri:
            if not r["won"] or r["action"] != "bank":
                bad.append((mask, faces, t, need, "should have won"))
        else:
            gbank = 1.0 + ctx.g[ri - ti2]
            ru = solver._need_roll_value(U, ctx.work, ctx.g, ri, r["next_mask"], ti2)
            want = "bank" if gbank <= ru else "reroll"
            if r["action"] != want:
                bad.append((mask, faces, t, need, f"{r['action']} != {want}"))
        # Bookkeeping: kept and rerolled dice must partition the throw.
        kept = sum(1 << i for i in r["keep"])
        legal = (kept | r["next_mask"] == mask if not r["hot"]
                 else kept == mask and r["next_mask"] == FULL_HAND)
        if not legal or (kept & r["next_mask"] and not r["hot"]):
            bad.append((mask, faces, t, need, "kept/reroll not a partition"))
        cmin, cmax = min(cmin, r["clear_prob"]), max(cmax, r["clear_prob"])
    check("advice == U_r argmin (400 random states)", bad, [])
    ok = 0.0 <= cmin <= cmax <= 1.0
    if not ok:
        FAILED.append(f"clear_prob out of [0,1]: {cmin}..{cmax}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'win-this-turn probability stays in [0,1]':<44} "
          f"{cmin:.3f}..{cmax:.3f}")


def test_need_strategy():
    """Need-aware strategy summaries: the far / mid / endgame readings.

    The points-max bank thresholds (5 dice -> 3400, 6 dice -> never) are wrong
    once the wager is close, because past the target points are worthless. These
    check the recomputed thresholds are conditioned on the remaining need --
    capped at it, tightening as it shrinks, and collapsing to "bank the moment you
    cover it" in the endgame -- and that the turn shape still balances. Small need
    levels keep the shared curve quick to build.
    """
    print("\nNeed-aware strategy summaries (far / mid / endgame)")
    fair = [DICE_PROBS["Ordinary die"]] * 6
    names = ["Ordinary die"] * 6
    work = solver._work_lists(fair)
    g = solver.need_curve(fair, max_target=800, work=work)["g"]
    levels = [("far", 800), ("mid", 400), ("end", 150)]
    strat = solver.need_strategy(fair, g, levels, work=work, names=names)
    by = {s["label"]: s for s in strat}

    check("three need levels reported", [s["label"] for s in strat],
          ["far", "mid", "end"])

    # Every threshold is capped at its need -- the policy never rolls past the win.
    over = [(s["label"], n, v["safe"]) for s in strat
            for n, v in s["bank_at"]["by_size"].items()
            if v["safe"] is not None and v["safe"] > s["need"]]
    check("no threshold exceeds its need", over, [])

    # The safe figure never overshoots a real hand of that size (carried-over).
    bad = []
    for s in strat:
        safe = {n: v["safe"] for n, v in s["bank_at"]["by_size"].items()}
        for h in s["bank_at"]["hands"]:
            if h["bank"] is not None and h["bank"] < (safe[h["n"]] or 0):
                bad.append((s["label"], h))
    check("safe threshold never overshoots a hand", bad, [])

    # Thresholds tighten as the need shrinks: the five-die stop point falls from
    # far to mid to endgame, where every hand collapses onto the need itself --
    # bank the moment the tally covers it. The points-max 3400 is gone.
    f5, m5, e5 = (by[k]["bank_at"]["by_size"][5]["safe"] for k in ("far", "mid", "end"))
    ok = f5 >= m5 >= e5 and f5 > e5
    if not ok:
        FAILED.append(f"five-die threshold should tighten far>=mid>=end: {f5},{m5},{e5}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'thresholds tighten toward the endgame':<44} "
          f"5 dice: {f5} -> {m5} -> {e5}")
    esafe = {n: by["end"]["bank_at"]["by_size"][n]["safe"] for n in range(1, 7)}
    check("endgame banks the moment the need is covered",
          all(v == 150 for v in esafe.values()), True)

    # Need-aware thresholds never exceed the points-max ones (None = never bank).
    V = solver.value_table(fair, work)
    evmax = solver._bank_thresholds(V, names)["by_size"]
    worse = [(n, by["far"]["bank_at"]["by_size"][n]["safe"], evmax[n]["safe"])
             for n in range(1, 7)
             if by["far"]["bank_at"]["by_size"][n]["safe"] is not None
             and evmax[n]["safe"] is not None
             and by["far"]["bank_at"]["by_size"][n]["safe"] > evmax[n]["safe"]]
    check("need-aware <= points-max thresholds", worse, [])

    # Every turn still ends somewhere (bank + bust = 1); banking the win makes the
    # endgame bust far less than pushing hard far from the target.
    for s in strat:
        tot = s["shape"]["bust"] + sum(v["bank"] for v in s["shape"]["by_size"].values())
        if abs(tot - 1.0) > 1e-9:
            FAILED.append(f"need-aware shape sums to {tot} at {s['label']}")
    print("  ok   need-aware turn outcomes sum to 1")
    fb, eb = by["far"]["shape"]["bust"], by["end"]["shape"]["bust"]
    ok = eb < fb
    if not ok:
        FAILED.append(f"endgame should bust less than far play: {eb} vs {fb}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'endgame busts less than far play':<44} "
          f"{eb * 100:.1f}% < {fb * 100:.1f}%")

    # Strong and impossible-face sets: thresholds stay within the need, and the
    # zero-probability pruning holds up under the min-objective for the Pie die.
    for nm in ("Weighted die", "Pie die"):
        dp = [DICE_PROBS[nm]] * 6
        wk = solver._work_lists(dp)
        gg = solver.need_curve(dp, max_target=800, work=wk)["g"]
        st = solver.need_strategy(dp, gg, levels, work=wk)
        bad = [(s["label"], n) for s in st
               for n, v in s["bank_at"]["by_size"].items()
               if v["safe"] is not None and v["safe"] > s["need"]]
        if bad:
            FAILED.append(f"{nm}: threshold over need at {bad}")
    print("  ok   strong and impossible-face sets stay within the need")


def test_ranking_matches_bruteforce():
    print("\nStage-1 ranking vs. direct computation")
    names = ["Ordinary die", "King's die", "Weighted die", "Pie die",
             "Grimy die", "Odd die", "Lucky die"]
    probs = [DICE_PROBS[n] for n in names]
    pool = [(p, 1) for p in probs]
    ranked = solver.rank_combinations(pool)
    check("every 6-subset ranked", len(ranked), 7)
    worst = 0.0
    for ev, counts in ranked:
        rows = []
        for i, c in enumerate(counts):
            rows.extend([probs[i]] * c)
        worst = max(worst, abs(ev - solver.first_roll_ev(rows)))
    ok = worst < 1e-9
    if not ok:
        FAILED.append(f"fast ranking disagrees with direct EV by {worst}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'shared-prefix DP == direct enumeration':<44} "
          f"max diff {worst:.2e}")


def test_pool_and_autofill():
    print("\nSelection pool: quantities and ordinary-die fill")
    check("7 distinct dice, one each", solver.count_multisets([1] * 7, 6), 7)
    check("3 distinct, six each", solver.count_multisets([6, 6, 6], 6), 28)
    check("nothing to pick from", solver.count_multisets([], 6), 0)

    # Owning two of a die must allow using both, and no more. max_target=1500
    # bounds the (now need-aware) solve so the selection logic can be tested
    # without paying for a full g-curve to 4000 on every candidate.
    res = solver.optimize({"Weighted die": 2}, DICE_PROBS, refine=4,
                          fill_die=DEFAULT_DIE, target=1500, max_target=1500)
    counts = {n: c for r in res["results"] for n, c in r["counts"]}
    over = [r["counts"] for r in res["results"]
            if dict(r["counts"]).get("Weighted die", 0) > 2]
    check("quantity cap respected", over, [])
    check("selections considered", res["combinations_ranked"], 3)

    # Results are ranked by need-aware g at the chosen wager, lowest first.
    gs = [r["g"][1500] for r in res["results"]]
    check("ranked ascending by g[target]", gs, sorted(gs))
    best = res["results"][0]
    check("best uses both special dice plus fill",
          sorted(best["counts"]), [["Ordinary die", 4], ["Weighted die", 2]])
    for r in res["results"]:
        if sum(c for _, c in r["counts"]) != 6:
            FAILED.append(f"selection is not six dice: {r['counts']}")
    print("  ok   every selection is exactly six dice")

    # An all-ordinary hand must reproduce the plain fair-dice value, and its
    # g[1500] must match the standalone need curve (optimize rounds g to 3 dp).
    allord = next(r for r in res["results"]
                  if dict(r["counts"]).get("Ordinary die") == 6)
    close("six ordinary dice == fair-dice baseline", allord["turn_ev"], 564.4, 0.2)
    close("optimize g[1500] == need_curve g[1500]", allord["g"][1500],
          solver.need_curve([DICE_PROBS["Ordinary die"]] * 6,
                            max_target=1500)["g"][30], 1e-3)

    # Without a fill die, fewer than six owned dice means nothing to solve.
    empty = solver.optimize({"Weighted die": 2}, DICE_PROBS, refine=4, fill_die=None)
    check("no fill die and too few owned -> no results", empty["results"], [])


def test_montecarlo(trials=200_000):
    """Play the solver's own policy at random and see if it earns what it claims."""
    print(f"\nMonte-Carlo validation ({trials:,} simulated turns per set)")
    ROW, STEP, NT = solver.ROW, solver.STEP, solver.NT
    mv = solver.moves()
    rng = random.Random(99)

    for name in ["Ordinary die", "Heavenly Kingdom die", "Die of misfortune"]:
        dp = [DICE_PROBS[name]] * 6
        V = solver.value_table(dp)
        exact = V[FULL_HAND * ROW]

        cums = []
        for pr in dp:
            c, s = [], 0.0
            for x in pr:
                s += x
                c.append(s)
            cums.append(c)

        total = sq = 0
        for _ in range(trials):
            hand, t = FULL_HAND, 0
            while True:
                ti = t // STEP
                if ti >= NT or V[hand * ROW + ti] <= t:
                    break
                oid = 0
                for j, die in enumerate(members(hand)):
                    r, c, f = rng.random(), cums[die], 0
                    while f < 5 and r >= c[f]:
                        f += 1
                    oid += f * 6 ** j
                opts = mv[hand][oid]
                if not opts:
                    t = 0
                    break
                best = max(((V[n * ROW + (t + p) // STEP], n, p) for n, p in opts),
                           key=lambda z: z[0])
                hand, t = best[1], t + best[2]
            total += t
            sq += t * t

        mean = total / trials
        sd = math.sqrt(max(sq / trials - mean * mean, 0))
        se = sd / math.sqrt(trials)
        z = abs(mean - exact) / se if se else 0.0
        ok = z < 4
        if not ok:
            FAILED.append(f"{name}: simulated {mean:.1f} vs exact {exact:.1f} (z={z:.1f})")
        print(f"  {'ok  ' if ok else 'FAIL'} {name:<28} exact {exact:8.1f}  "
              f"simulated {mean:8.1f} +/-{se:.1f}  (z={z:.2f})")


if __name__ == "__main__":
    test_scoring()
    test_selection_enumeration()
    test_dice_data()
    test_probability_tables()
    test_solver_values()
    test_turns_to_win()
    test_turn_distribution()
    test_need_curve()
    test_advisor()
    test_need_strategy()
    test_ranking_matches_bruteforce()
    test_pool_and_autofill()
    if "--slow" in sys.argv:
        test_need_curve_pin()
        test_montecarlo()

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILURE(S):")
        for f in FAILED:
            print(f"  - {f}")
        sys.exit(1)
    print("All tests passed.")

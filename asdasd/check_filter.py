"""Audit the shortlist funnel against the need-aware objective.

The app narrows every 6-dice set to a handful worth an exact need-aware solve in
two steps: first-roll value (free) picks a wide shortlist, then an exact turn_ev
solve (cheap) picks the finalists. g[target] -- the fewest expected turns to reach
a wager -- is the true objective, so the "best" set MINIMISES it. Either cut can
miss it, and this script measures how deep the g-optimum sits in each ordering.

Two modes:

  Exhaustive (small inventories): brute-force g over EVERY set and report where
  the optimum sat in the first-roll ordering, plus what each shortlist size found.

      python check_filter.py                  # 9-die inventories (slow)
      python check_filter.py --size 7 -n 1    # smaller and quicker

  Funnel (large inventories): the flooding case the toys miss. First-roll value
  over ~40k sets crowds the top with near-duplicate strong sets, so the g-optimum
  can rank deep on it yet shallow on turn_ev. This turn_ev-sieves a wide first-roll
  head and full-solves a generous net, then reports the optimum's position in BOTH
  orderings -- the depth first_roll_keep(total) and the turn_ev sieve must clear.

      python check_filter.py --funnel                  # default 15-die strong inv
      python check_filter.py --funnel --target 4000

Each need-aware solve is far dearer than one turn_ev (a whole g-curve per set), so
both modes fan the sets out across the shared solver pool.
"""

import argparse
import random
import time

from farkle import solver
from farkle.dice import DICE_PROBS, DICE_NAMES

SHORTLIST_SIZES = (4, 8, 12, 20, 30)
AUDIT_TARGETS = (1500, 4000)   # short and long: the proxy may rank them differently

# Heavy in 1/5-loaded dice: their first-roll values cluster, so many near-identical
# strong sets crowd the top of the cheap ordering -- exactly what buries the true
# g-optimum below a first-roll-only shortlist. This is the case the small toys miss.
STRONG_INV = [
    "Weighted die", "Heavenly Kingdom die", "Favourable die", "Grimy die",
    "Painted die", "Monk's die", "Lucky die", "Holy Trinity die",
    "Sad Greaser's die", "Mathematician's die", "King's die", "Odd die",
    "Cautious cheater's die", "Strip die", "Grozav's lucky die",
]


def study(inv, label):
    probs = [DICE_PROBS[n] for n in inv]
    pool = [(p, 1) for p in probs]              # one of each die
    ranked = solver.rank_combinations(pool)     # cheap first-roll ordering, best first

    def rows_of(counts):                        # a set's six probability rows
        rows = []
        for i, c in enumerate(counts):
            rows.extend([probs[i]] * c)
        return rows

    t0 = time.time()
    workers = solver.shared_pool()
    jobs = [(rows_of(counts), AUDIT_TARGETS) for _, counts in ranked]
    gvals = list(workers.map(solver._need_targets, jobs))   # {target: g} per set, rank order
    elapsed = time.time() - t0

    print(f"\n{label}: {len(inv)} dice -> {len(ranked)} sets "
          f"(need-aware solve of all took {elapsed:.0f}s)")

    worst = 0
    for tgt in AUDIT_TARGETS:
        col = [g[tgt] for g in gvals]           # g[tgt] of every set, in first-roll order
        best_i = min(range(len(col)), key=lambda i: col[i])
        best_g, position = col[best_i], best_i + 1   # ranked is already best-first
        worst = max(worst, position)
        print(f"  target {tgt}: true best g={best_g:.3f} "
              f"(first-roll EV {ranked[best_i][0]:.0f}) ranked #{position} "
              f"of {len(ranked)} on first-roll value")
        for k in SHORTLIST_SIZES:
            got = min(col[:k], default=float("inf"))     # best g within the top-k shortlist
            loss = got - best_g
            verdict = "OPTIMAL" if loss <= 1e-9 else f"{loss:.3f} turns worse"
            print(f"    shortlist {k:<3} -> g {got:7.3f}  {verdict}")
    return worst


def study_funnel(inv, label, target, head=400, net=80):
    """Where the g-optimum sits in the first-roll vs turn_ev orderings, on a big inv.

    Full-solving all ~40k sets is infeasible, so this brackets the optimum: it
    turn_ev-sieves the first-roll top `head` (the optimum is not plausibly below a
    first-roll rank of several hundred, given how tightly the two proxies agree),
    then full-solves the union of the first-roll top `net` and the turn_ev top
    `net`. The best g in that union is the audit optimum; its rank in each ordering
    is what a funnel stage must keep to hold on to it.
    """
    probs = [DICE_PROBS[n] for n in inv]
    pool = [(p, 1) for p in probs]              # one of each die
    ranked = solver.rank_combinations(pool)     # first-roll order, best first

    def rows_of(counts):
        rows = []
        for i, c in enumerate(counts):
            rows.extend([probs[i]] * c)
        return rows

    workers = solver.shared_pool()
    # Sieve past the funnel's own first-roll net so the audit can see an optimum
    # that sits just outside it -- the exact miss this script exists to catch.
    head = min(len(ranked), max(head, solver.first_roll_keep(len(ranked)) + 200))
    t0 = time.time()
    snt = solver.sieve_nt(target)                         # target-aligned sieve horizon
    tev = list(workers.map(solver._turn_ev_one,
                           [(i, rows_of(ranked[i][1]), snt) for i in range(head)]))
    tev = {i: v for i, v in tev}                          # first-roll index -> turn_ev
    turn_order = sorted(range(head), key=lambda i: -tev[i])  # first-roll idx, by turn_ev

    net = min(net, head)
    cand = sorted(set(range(net)) | set(turn_order[:net]))   # first-roll indices
    gmap = dict(zip(cand, workers.map(
        solver._need_targets, [(rows_of(ranked[i][1]), (target,)) for i in cand])))
    gvals = {i: g[target] for i, g in gmap.items()}
    elapsed = time.time() - t0

    best_i = min(cand, key=lambda i: gvals[i])
    best_g = gvals[best_i]
    fr_pos = best_i + 1                          # rank in first-roll order
    te_pos = turn_order.index(best_i) + 1        # rank in turn_ev order

    print(f"\n{label}: {len(inv)} dice -> {len(ranked)} sets, target {target} "
          f"(sieved {head}, solved {len(cand)} in {elapsed:.0f}s)")
    print(f"  g-optimum g={best_g:.3f}  first-roll rank #{fr_pos}  turn_ev rank #{te_pos}")
    # What the old single first-roll cut would have shipped, vs the turn_ev sieve.
    for k in SHORTLIST_SIZES:
        fr_keep = [i for i in cand if i < k]                 # first-roll top k
        te_keep = set(turn_order[:k])                        # turn_ev top k
        fr_best = min((gvals[i] for i in fr_keep), default=float("inf"))
        te_best = min((gvals[i] for i in cand if i in te_keep), default=float("inf"))
        fr_v = "OPTIMAL" if fr_best <= best_g + 1e-9 else f"{fr_best - best_g:.3f} worse"
        te_v = "OPTIMAL" if te_best <= best_g + 1e-9 else f"{te_best - best_g:.3f} worse"
        print(f"    top {k:<3}  first-roll -> {fr_v:<14}  turn_ev -> {te_v}")
    fr_keep = solver.first_roll_keep(len(ranked))   # adaptive first-roll net
    te_keep = 20                                     # a generous displayed depth
    ok = fr_pos <= fr_keep and te_pos <= te_keep
    print(f"  funnel keeps (first-roll {fr_keep} = first_roll_keep({len(ranked)}), "
          f"turn_ev {te_keep}): {'OK' if ok else 'MISS -- widen a stage'}")
    return fr_pos, te_pos


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--funnel", action="store_true",
                    help="audit the two-stage funnel on a large strong inventory")
    ap.add_argument("--target", type=int, default=1500, help="wager to audit (funnel mode)")
    ap.add_argument("--size", type=int, default=None,
                    help="dice per inventory (exhaustive: 9; funnel: all of STRONG_INV)")
    ap.add_argument("-n", type=int, default=2, help="random inventories to test")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    solver.warm_up()   # build the move table and start the workers once, up front

    if args.funnel:
        inv = STRONG_INV[:args.size] if args.size else STRONG_INV
        fr, te = study_funnel(inv, "strong 1/5-loaded", args.target)
        print(f"\nFunnel must keep first-roll rank #{fr} and turn_ev rank #{te}. "
              f"turn_ev pulls the optimum from first-roll #{fr} up to #{te}: the "
              f"whole point of the sieve.")
        return

    size = args.size or 9
    positions = []
    if size == 9:
        positions.append(study(
            ["Heavenly Kingdom die", "Lucky die", "Favourable die", "Aranka's die",
             "Odd die", "Ordinary die", "King's die", "Painted die", "Grimy die"],
            "hand-picked strong"))

    rng = random.Random(args.seed)
    for i in range(args.n):
        positions.append(study(rng.sample(DICE_NAMES, size),
                               f"random {size}-die #{i + 1}"))

    worst = max(positions)
    print(f"\nWorst position of the g-optimum in the cheap ordering: #{worst} "
          f"(over {len(positions)} inventories, targets {AUDIT_TARGETS})")
    if worst <= 8:
        print(f"Default shortlist of 8 has a margin of {8 - worst} places.")
    elif worst <= 20:
        print(f"Exceeds a shortlist of 8, but the app default (refine=20) still "
              f"covers it by {20 - worst} places.")
    else:
        print(f"Exceeds even 20 -- raise the shortlist default above #{worst}.")


if __name__ == "__main__":
    main()

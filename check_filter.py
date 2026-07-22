"""Audit the shortlist heuristic against the need-aware objective.

The app ranks every 6-dice set by first-roll value (cheap) and then solves only
the top few exactly. That is the one approximation in the pipeline: if the true
best set ranks poorly on first-roll value, the shortlist misses it.

The exact metric is now need-aware g[target] -- the fewest expected turns to reach
a wager -- so the "true best" set is the one MINIMISING g[target]. This script
brute-forces g over EVERY set for small inventories and reports where that optimum
sat in the cheap ordering, and what each shortlist size would have found. It
audits at a short (1500) and a long (4000) target, since the first-roll proxy may
correlate differently with each.

    python check_filter.py                  # 9-die inventories (slow)
    python check_filter.py --size 7 -n 1    # smaller and quicker

Each need-aware solve is far dearer than the old single turn_ev (a whole g-curve
per set), so this fans the sets out across the shared solver pool. Keep to small
inventories.
"""

import argparse
import random
import time

from farkle import solver
from farkle.dice import DICE_PROBS, DICE_NAMES

SHORTLIST_SIZES = (4, 8, 12, 20, 30)
AUDIT_TARGETS = (1500, 4000)   # short and long: the proxy may rank them differently


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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=9, help="dice per inventory")
    ap.add_argument("-n", type=int, default=2, help="random inventories to test")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    solver.warm_up()   # build the move table and start the workers once, up front

    positions = []
    if args.size == 9:
        positions.append(study(
            ["Heavenly Kingdom die", "Lucky die", "Favourable die", "Aranka's die",
             "Odd die", "Ordinary die", "King's die", "Painted die", "Grimy die"],
            "hand-picked strong"))

    rng = random.Random(args.seed)
    for i in range(args.n):
        positions.append(study(rng.sample(DICE_NAMES, args.size),
                               f"random {args.size}-die #{i + 1}"))

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

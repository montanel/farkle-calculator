"""Audit the shortlist heuristic.

The app ranks every 6-dice set by first-roll value (cheap) and then solves only
the top few with the full turn DP. That is the one approximation in the
pipeline: if the true best set ranks poorly on first-roll value, the shortlist
misses it.

This script brute-forces the full DP over EVERY set for small inventories and
reports where the true optimum sat in the cheap ordering, and what each
shortlist size would have found.

    python check_filter.py                  # 9-die inventories  (~4 min)
    python check_filter.py --size 11 -n 2   # bigger and slower  (~8 min each)
"""

import argparse
import random
import time

from farkle import solver
from farkle.dice import DICE_PROBS, DICE_NAMES

SHORTLIST_SIZES = (4, 8, 12, 20, 30)


def study(inv, label):
    probs = [DICE_PROBS[n] for n in inv]
    ranked = solver.rank_combinations(probs)  # the cheap ordering the app uses

    t0 = time.time()
    full = []
    for ev1, idx in ranked:
        te = solver.turn_ev([probs[i] for i in idx])["turn_ev"]
        full.append((te, ev1, idx))
    full.sort(key=lambda r: -r[0])

    best_te, best_ev1, best_idx = full[0]
    position = next(i for i, (_, ix) in enumerate(ranked) if ix == best_idx) + 1

    print(f"\n{label}: {len(inv)} dice -> {len(ranked)} sets "
          f"(full solve took {time.time() - t0:.0f}s)")
    print(f"  true best turn EV {best_te:.0f} (first-roll EV {best_ev1:.0f})")
    print(f"  it ranked #{position} of {len(ranked)} on first-roll value")
    for k in SHORTLIST_SIZES:
        top = {ix for _, ix in ranked[:k]}
        got = max((te for te, _, ix in full if ix in top), default=0.0)
        loss = best_te - got
        verdict = "OPTIMAL" if loss <= 1e-9 else f"{loss:.0f} short ({loss / best_te * 100:.1f}%)"
        print(f"    shortlist {k:<3} -> {got:7.0f}  {verdict}")
    return position


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=9, help="dice per inventory")
    ap.add_argument("-n", type=int, default=2, help="random inventories to test")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    positions = []
    if args.size == 9:
        positions.append(study(
            ["Heavenly Kingdom die", "Lucky Die", "Favourable die", "Aranka's die",
             "Odd die", "Ordinary die", "King's die", "Painted die", "Grimy die"],
            "hand-picked strong"))

    rng = random.Random(args.seed)
    for i in range(args.n):
        positions.append(study(rng.sample(DICE_NAMES, args.size),
                               f"random {args.size}-die #{i + 1}"))

    worst = max(positions)
    print(f"\nWorst position of the true optimum in the cheap ordering: "
          f"#{worst} (over {len(positions)} inventories)")
    print(f"Default shortlist is 8, so the margin here is {8 - worst} places.")


if __name__ == "__main__":
    main()

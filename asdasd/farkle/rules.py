"""KCD2 Farkle scoring rules and the precomputed move table.

The move table is the heart of the solver. It answers, for every possible
"which dice are still in my hand" + "what did they land on" pair, the question
*what are all my legal moves and what is each one worth?*

Crucially this table depends only on the rules, never on which physical dice
you own, so it is built once and reused for every 6-dice combination we score.
"""

import hashlib
from functools import lru_cache
from itertools import combinations, product

# --- Scoring table (KCD2 wiki) ------------------------------------------------
# Three of a kind; each die beyond the third doubles the value.
TRIPLE_BASE = {1: 1000, 2: 200, 3: 300, 4: 400, 5: 500, 6: 600}
SINGLES = {1: 100, 5: 50}
STRAIGHTS = [((1, 2, 3, 4, 5, 6), 1500), ((2, 3, 4, 5, 6), 750), ((1, 2, 3, 4, 5), 500)]

FULL_HAND = 0b111111  # all six dice in hand


def rules_signature():
    """A short, stable hash of the scoring rules.

    A cached solve is only valid while the rules that produced it stand, so this
    goes into every disk-cache key. Any edit to the scoring tables changes the
    digest and so silently invalidates every stale entry.
    """
    payload = repr((sorted(TRIPLE_BASE.items()), sorted(SINGLES.items()),
                    STRAIGHTS)).encode()
    return hashlib.sha1(payload).hexdigest()[:12]


def _decompositions(counts):
    """Yield (points, remaining_counts) for every scoring combo playable now."""
    for faces, points in STRAIGHTS:
        if all(counts[f - 1] for f in faces):
            rest = list(counts)
            for f in faces:
                rest[f - 1] -= 1
            yield points, tuple(rest)

    for face in range(1, 7):
        have = counts[face - 1]
        for n in range(3, have + 1):  # n-of-a-kind, doubling past the third die
            rest = list(counts)
            rest[face - 1] -= n
            yield TRIPLE_BASE[face] * 2 ** (n - 3), tuple(rest)
        if face in SINGLES and have:
            rest = list(counts)
            rest[face - 1] -= 1
            yield SINGLES[face], tuple(rest)


@lru_cache(maxsize=None)
def best_score(counts):
    """Max points for setting aside EVERY die in `counts` (a 6-tuple of counts).

    Returns None when the multiset cannot be fully decomposed into scoring
    combos -- i.e. it is not a legal set-aside. Recursive because greedy gets it
    wrong: [1,1,2,3,4,5] is worth 600 (straight + spare 1), not 250 (singles).
    """
    if sum(counts) == 0:
        return 0
    best = None
    for points, rest in _decompositions(counts):
        sub = best_score(rest)
        if sub is not None and (best is None or points + sub > best):
            best = points + sub
    return best


@lru_cache(maxsize=None)
def best_take(counts):
    """Max points obtainable from a roll of `counts`, leaving the rest behind.

    Same recursion as best_score but stopping early is always allowed, so this
    never returns None -- 0 means the roll is a Farkle.
    """
    best = 0
    for points, rest in _decompositions(counts):
        total = points + best_take(rest)
        if total > best:
            best = total
    return best


def get_all_possible_combinations(dice_values):
    """Every legal set-aside from one roll, as (indices, points) pairs.

    `dice_values` is a list of faces (1-6), one per die still in hand. Indices
    are positions into that list -- two selections worth the same points but
    using different physical dice are kept separate, because they leave you
    rerolling dice with different distributions. An empty result is a Farkle.
    """
    out = []
    for size in range(1, len(dice_values) + 1):
        for idx in combinations(range(len(dice_values)), size):
            counts = [0] * 6
            for i in idx:
                counts[dice_values[i] - 1] += 1
            points = best_score(tuple(counts))
            if points is not None:
                out.append((idx, points))
    return out


# --- Move table ---------------------------------------------------------------
# For a hand `mask` (bit i set = die i still available), the dice in it are
# `members(mask)`. An outcome is a face assignment to those dice, encoded as
#     outcome_id = sum(face_j * 6**j)   for j indexing into members(mask)
# so that member 0's face varies fastest.


def members(mask):
    return [i for i in range(6) if mask >> i & 1]


def build_move_table():
    """moves[mask][outcome_id] -> list of (next_mask, points).

    `next_mask` is the hand you are left with. Setting aside all six dice is
    "hot dice": the hand refills, so next_mask becomes FULL_HAND rather than 0.
    """
    moves = [None] * 64
    for mask in range(1, 64):
        mem = members(mask)
        k = len(mem)
        # Subsets of positions, precomputed once per hand size.
        subsets = []
        for size in range(1, k + 1):
            subsets.extend(combinations(range(k), size))

        table = []
        for faces in product(range(1, 7), repeat=k):
            # `faces` is indexed by position j; enumerate in outcome_id order.
            opts = []
            for sub in subsets:
                counts = [0] * 6
                for j in sub:
                    counts[faces[j] - 1] += 1
                points = best_score(tuple(counts))
                if points is None:
                    continue
                nxt = mask
                for j in sub:
                    nxt &= ~(1 << mem[j])
                opts.append((nxt if nxt else FULL_HAND, points))
            table.append(opts)

        # product() varies the LAST element fastest, but outcome_id wants the
        # first to vary fastest, so reindex.
        reordered = [None] * len(table)
        for i, opts in enumerate(table):
            digits = []
            n = i
            for _ in range(k):
                digits.append(n % 6)
                n //= 6
            digits.reverse()  # digits[j] is member j's face-1
            oid = sum(d * 6 ** j for j, d in enumerate(digits))
            reordered[oid] = opts
        moves[mask] = reordered
    return moves


def outcome_probabilities(mask, dice_probs):
    """Probability of each outcome_id for `mask`, given per-die face probs."""
    dist = [1.0]
    for j, die in enumerate(members(mask)):
        pr = dice_probs[die]
        block = len(dist)
        nxt = [0.0] * (block * 6)
        for f in range(6):
            pf = pr[f]
            if pf:
                base = f * block
                for oi in range(block):
                    nxt[base + oi] = dist[oi] * pf
        dist = nxt
    return dist

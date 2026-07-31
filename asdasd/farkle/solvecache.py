"""Disk cache for solved dice sets.

Inventories grow one die at a time, so a run mostly re-solves sets an earlier
run already paid for -- a full need curve is tens of seconds, so re-deriving
yesterday's answers dominates. Each solved set is keyed by its sorted die names
plus a signature of the rules and discretisation that produced it, so a cached
value is reused only while it is still exact; edit the scoring table or the grid
and every stale entry's key changes with it.

Entries are one small pickle apiece under a cache directory. Writes are atomic
(temp file, then rename) so an interrupted run never leaves a half-written file a
later run would read as valid, and reads treat any error as a miss. Set
FARKLE_NO_CACHE=1 to bypass the cache entirely (used by the test suite).
"""

import hashlib
import os
import pickle
import tempfile
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".solve_cache"


def enabled():
    return os.environ.get("FARKLE_NO_CACHE") != "1"


def _path(kind, sig, names, max_target):
    label = f"{sig}|{kind}|{max_target}|{'+'.join(sorted(names))}"
    digest = hashlib.sha1(label.encode()).hexdigest()
    return CACHE_DIR / kind / f"{digest}.pkl"


def get(kind, sig, names, max_target=0):
    """Cached value for one set, or None on a miss (or any read error)."""
    if not enabled():
        return None
    try:
        with open(_path(kind, sig, names, max_target), "rb") as f:
            return pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError, ValueError):
        return None


def put(kind, sig, names, value, max_target=0):
    """Store one set's solved value. Best effort: cache trouble never fails a solve."""
    if not enabled():
        return
    p = _path(kind, sig, names, max_target)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except (OSError, NameError, UnboundLocalError):
            pass

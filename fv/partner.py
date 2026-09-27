"""
A partner's players, so your lineups do not collide with theirs.

## Why this exists

When two people split winnings, the pair is one portfolio. Expected value is
unchanged by overlap -- two correlated entries win the same money on average as
two independent ones -- but the chance that AT LEAST ONE of them hits is not,
and a top-heavy tournament pays only for that. Eleven lineups between two people
who share ten players is not eleven independent shots.

It is the same mechanism the project already measured inside a single portfolio:
ten near-identical lineups score about 20 points worse at their best than ten
different ones. `core-variants` in the Strategy Ledger.

## Why it excludes rather than penalises

A soft penalty needs a weight, and there is no measurement to set one with. A
hard exclusion needs no parameter and is exactly right when the partner has
genuinely committed to those players: his lineups are locked, so for your
purposes he owns them. On the Week 3 board excluding 35 of his players left 502
of 537 -- the pool barely notices, and the board that comes out projects HIGHER
than the hand-edited one it replaces, because the optimiser is choosing from
what is left rather than swapping one name at a time.

## The file

`data/partner-players.json`, a list of names, refreshed weekly by
`scripts/extract_partner.py` from whatever tracker the partner keeps. Absent is
a supported state and the feature simply switches off -- the file is deliberately
NOT committed, because a partner's picks are his data, not ours to publish.
"""
from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> dict:
    """
    The partner's committed players, or an empty record when there is no file.

    Returns `{"players": [...], "captured": ..., "note": ...}` so a caller can
    date it on screen. A stale partner file is worse than none: excluding the
    players he had LAST week shrinks the pool for no reason.
    """
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"players": [], "captured": None, "source": None}
    if isinstance(d, list):
        d = {"players": d}
    d.setdefault("players", [])
    d.setdefault("captured", None)
    return d


def exclude(pool: list[dict], partner: dict | None) -> tuple[list[dict], list[str]]:
    """
    Drop the partner's players from the pool. Returns the pool and who went.

    Matching is on the exact DraftKings name, because the partner's tracker and
    the salary file are both keyed on it. Anything that does not match is
    reported rather than silently ignored -- a name that fails to match is a
    player who stays available and quietly recreates the overlap.
    """
    names = {n for n in (partner or {}).get("players", []) if n}
    if not names:
        return list(pool), []
    have = {p["name"] for p in pool}
    dropped = sorted(names & have)
    missing = sorted(names - have)
    return [p for p in pool if p["name"] not in names], missing

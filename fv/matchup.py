"""
Defence vs position, read from `matchup-2026.json`.

That file is built by `app/scripts/fetch-matchup-2026.py` from this season's
own completed games (nflverse), not from the bought, September-dated
`dvp-2026.json` snapshot. See that script's docstring for why. This module
only attaches the already-computed number to a pool row; it does no fetching
and no fitting.

Rank 1 = the defence has allowed the most DK points to this position this
season (the softest matchup); 32 = the fewest (the toughest). A player whose
opponent has fewer than a handful of games logged still gets a number -- the
game count rides along so a thin sample is visible, not hidden.
"""
from __future__ import annotations


def attach(rows: list[dict], data: dict | None) -> list[dict]:
    """
    Return `rows` with `matchup_rank`, `matchup_vs_avg`, and `matchup_games`
    added where the opponent and position are covered. DST and anyone whose
    opponent is missing from the table are left without the fields rather
    than given an invented average.
    """
    positions = (data or {}).get("positions") or {}
    out = []
    for r in rows:
        pos = (r.get("position") or "").upper()
        opp = (r.get("opponent") or "").upper()
        entry = positions.get(pos, {}).get("teams", {}).get(opp)
        if entry:
            r = {**r, "matchup_rank": entry["rank"],
                 "matchup_vs_avg": entry["versusAverage"],
                 "matchup_games": entry["games"]}
        out.append(r)
    return out

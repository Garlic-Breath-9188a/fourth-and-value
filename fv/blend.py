"""
Blend this season's games with last season's average.

DraftKings ships `AvgPointsPerGame`, and in Week 2 that is ONE GAME. Bryce Young
read 35.4 off a single Sunday against a 2025 season of 14.9, and the optimizer
built around him because at his price a 35-point projection is unbeatable value.
It is not a projection, it is one result.

Measured over 40 early-season slates and 8,916 player-weeks, 2018-2025:

    DraftKings' own average     6.091 MAE
    prior season only           5.827
    blend, k = 3                5.550    -0.541  (95% CI -0.690 to -0.393)

For scale, the matchup adjustment already in this app is worth 0.0122 MAE per
player-week. This is about forty times larger, because it corrects a crude error
rather than refining a decent one. It matters most where there is least new
data: week 2 goes from 7.11 to 5.78, roughly twelve points across a roster.

    blend = w * (this season) + (1 - w) * (last season)
    w     = n / (n + K)        n = games played this season

K is how many games of the current season equal the prior season. K = 3 puts
week 2 at 25% and week 5 at 57%, which is what a per-week sweep independently
picked. Values 2-4 are statistically indistinguishable, so 3 sits in the middle
of a flat optimum. Do not tune it without a new measurement.

Mirrors app/lib/projectionBlend.ts. Keep them in step.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

SHRINKAGE_K = 3

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)


def key(name: str) -> str:
    """Join key. Mirrors cleanName in the TypeScript build, suffixes included."""
    return "".join(c for c in _SUFFIX.sub("", name or "").lower() if c.isalpha())


def load_prior(path: Path) -> dict:
    """Last season's scoring, or an empty record when the file is absent."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"season": None, "players": {}}
    d.setdefault("players", {})
    return d


def games_played_before(week: int) -> int:
    """
    How many games this season a DraftKings average represents.

    The export does not say, so it is inferred from the week. A player who
    missed time has fewer, and this overstates his sample -- erring toward
    trusting the current season slightly too much, which is the safer direction
    when the alternative is trusting a single game completely.
    """
    return max(0, int(week) - 1)


def current_weight(n: int, k: int = SHRINKAGE_K) -> float:
    return 0.0 if n <= 0 else n / (n + k)


def blend_value(current_mean, current_games: int, prior_mean, k: int = SHRINKAGE_K):
    """None only when neither side exists, so 'no information' differs from zero."""
    has_cur = current_mean is not None and current_games > 0
    has_prior = prior_mean is not None
    if not has_cur and not has_prior:
        return None
    if not has_prior:
        return current_mean
    if not has_cur:
        return prior_mean
    w = current_weight(current_games, k)
    return w * current_mean + (1 - w) * prior_mean


def apply_blend(rows: list[dict], prior: dict, week: int, k: int = SHRINKAGE_K) -> list[dict]:
    """
    Blend a pool in place of DraftKings' average, labelling every player.

    A projection that silently changed basis between weeks would be impossible
    to debug from the board, so each row records which sources produced it.
    """
    n = games_played_before(week)
    season = prior.get("season")
    if n <= 0 or not prior.get("players"):
        for r in rows:
            r["projection_source"] = "DraftKings season average"
        return rows

    w = current_weight(n, k)
    for r in rows:
        hit = prior["players"].get(key(r["name"]))
        if not hit or not hit.get("games"):
            r["projection_source"] = f"DK average only (no {season} data)"
            continue
        value = blend_value(r.get("projection"), n, hit.get("mean"), k)
        if value is not None:
            r["projection"] = value
        r["projection_source"] = (
            f"{w * 100:.0f}% this season ({n}g) + {(1 - w) * 100:.0f}% {season} ({hit['games']}g)"
        )
    return rows

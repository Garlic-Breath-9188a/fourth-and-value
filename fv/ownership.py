"""
Estimate what share of the field will draft each player.

This is a port of `app/lib/ownershipModel.ts` -- the fitting happens once in
that TypeScript module, against 6,007 player-slate rows from 15 real 2020
slates, and the fitted coefficients are exported to `ownership-coefficients.json`
by `app/scripts/export-ownership-coeffs.mjs`. This module only replays the
predict side (score, then renormalise within position) against this week's
pool. Re-run the export script and copy the file here if the training data or
temperature ever changes; do not hand-edit the coefficients.

Measured against real DraftKings DRAFT% (app/evidence/ownership-model-2020-holdout.txt,
ownership-model-2026-oos.txt): MAE 2.65-6.30 against a 3.35-7.29 proxy, six
years out of sample. Not perfect -- it is a predicted share, not a fact -- but
it is the best this project has and it previously lived only in a test script
nobody playing a live slate ever saw.

## Where this build's inputs are a true match to the fitted features, and where they are not

    feature         source here                              true match?
    logSalary       DK salary export                         yes
    salaryPctileInPos  rank within position, this slate       yes
    valuePerK       projection / (salary / 1000)              yes
    trailingPts     `projection` (season blend, see blend.py) approximate --
                                                               the model was
                                                               fitted on a
                                                               trailing 5-game
                                                               average; this
                                                               build has no
                                                               per-game points
                                                               log, so the
                                                               blended
                                                               season
                                                               projection
                                                               stands in
    trailingTgts    unavailable -- always 0                   missing
    trailingTouches `usage-2026.json` touches (most recent     approximate --
                    game only, not a 5-game average)           one game, not five
    trailingSnap    `usage-2026.json` snapPct (most recent     approximate, same
                    game only)                                 reason
    home            team == home team of `game`                yes
    dome            `context.ROOF` lookup                      yes

Missing values become 0, same as the TypeScript side -- the intercept absorbs
it. A player with no usage record still gets an ownership estimate; it is
built from salary and projection alone, same as the model would for anyone
the training data had never seen.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import context as context_mod
from .blend import key as name_key

COEFFICIENTS_PATH = Path(__file__).parent / "ownership-coefficients.json"

# Roster slots per position, measured from 29 real contests. Mirrors
# app/lib/ownershipModel.ts ROSTER_SLOTS.
ROSTER_SLOTS = {"QB": 1.00, "RB": 2.41, "WR": 3.36, "TE": 1.08, "DST": 1.00}

FEATURE_ORDER = (
    "logSalary", "salaryPctileInPos", "valuePerK", "trailingPts",
    "trailingTgts", "trailingTouches", "trailingSnap", "home", "dome",
)


def load_model(path: Path = COEFFICIENTS_PATH) -> dict:
    return json.loads(path.read_text())


def _featurise(row: dict) -> list[float]:
    return [
        math.log(max(1, row.get("salary") or 0)),
        row.get("salaryPctileInPos") or 0,
        row.get("valuePerK") or 0,
        row.get("trailingPts") or 0,
        row.get("trailingTgts") or 0,
        row.get("trailingTouches") or 0,
        row.get("trailingSnap") or 0,
        1 if row.get("home") else 0,
        1 if row.get("dome") else 0,
    ]


def _score(model: dict, row: dict) -> float:
    coeffs = model["byPosition"].get(row["position"])
    if not coeffs:
        return 0.0
    x = _featurise(row)
    return coeffs["intercept"] + sum(b * v for b, v in zip(coeffs["beta"], x))


def predict_slate(model: dict, rows: list[dict], cap: float = 100.0) -> list[float]:
    """
    Predicted ownership, in percent, one per row, normalised so each
    position's total equals its roster slots. Mirrors
    `ownershipModel.ts::predictSlate`, cap-redistribution loop included.
    """
    out = [0.0] * len(rows)
    by_pos: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_pos.setdefault(r["position"], []).append(i)

    temperature = model.get("temperature", 1)
    for pos, idx in by_pos.items():
        slots = ROSTER_SLOTS.get(pos, 1)
        target = slots * 100
        raw = [math.exp(_score(model, rows[i]) * temperature) for i in idx]
        total = sum(raw) or 1
        share = [(v / total) * target for v in raw]

        for _ in range(50):
            over = sum(max(0, v - cap) for v in share)
            if over < 1e-9:
                break
            room_idx = [j for j, v in enumerate(share) if v < cap]
            room = sum(cap - share[j] for j in room_idx)
            share = [min(cap, v) for v in share]
            if room < 1e-9:
                break
            for j in room_idx:
                share[j] += over * ((cap - share[j]) / room)

        for j, i in enumerate(idx):
            out[i] = share[j]
    return out


def attach(rows: list[dict], usage: dict | None, model: dict | None = None) -> list[dict]:
    """
    Return `rows` with an `ownership` percentage added to each, estimated from
    salary, projection, and -- where a usage record exists -- last game's
    snap share and touches. Every row gets an estimate; nothing is dropped,
    because a lineup builder needs a number for every player, not just the
    ones with a full feature set.
    """
    model = model or load_model()
    players = (usage or {}).get("players") or {}

    by_pos: dict[str, list[dict]] = {}
    for r in rows:
        by_pos.setdefault((r.get("position") or "").upper(), []).append(r)

    salary_rank: dict[str, float] = {}
    for pos, group in by_pos.items():
        salaries = sorted({r.get("salary") or 0 for r in group}, reverse=True)
        n = len(salaries)
        for r in group:
            rank = salaries.index(r.get("salary") or 0)
            salary_rank[r["id"]] = 1 - rank / (n - 1) if n > 1 else 1.0

    feature_rows = []
    for r in rows:
        pos = (r.get("position") or "").upper()
        salary = r.get("salary") or 0
        projection = r.get("projection") or 0
        u = players.get(name_key(r.get("name") or ""))
        host = context_mod.home_team(r.get("game") or "")
        feature_rows.append({
            "id": r.get("id"),
            "position": pos,
            "salary": salary,
            "salaryPctileInPos": salary_rank.get(r["id"], 1.0),
            "valuePerK": (projection / (salary / 1000)) if salary else 0,
            "trailingPts": projection,
            "trailingTgts": 0,
            "trailingTouches": (u or {}).get("touches"),
            "trailingSnap": (u or {}).get("snapPct"),
            "home": bool(host) and r.get("team") == host,
            "dome": context_mod.ROOF.get(host) == "dome",
        })

    ownership = predict_slate(model, feature_rows)
    return [{**r, "ownership": own} for r, own in zip(rows, ownership)]

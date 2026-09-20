"""
Which players has DraftKings mispriced -- and can you tell before lock?

Week 1 2026 prompted the question: the lineups that won carried cheap players
who hugely outscored their price. Spotting that afterwards is free. This module
is the part that survived an attempt to spot it beforehand.

## What "mispriced" means

Within a position, points rise with price, so the price-implied expectation is a
fitted line

    expected(points) = intercept + slope * (salary / 1000)

and the residual -- actual minus expected -- is how much a player beat his
price. `PRICE_LINE` holds that line per position, fitted over 35,384 player-weeks
from 2017-2025, along with the residual SD so an edge can be quoted in SDs or in
points.

## What predicts the residual, and what does not

Measured in `app/scripts/test-salary-mispricing.mjs`; raw output in
`app/evidence/salary-mispricing-2026-09-20.txt`. Trained on 2017-2023 and scored
on 2024-2025, which the fit never saw.

    predictor group              out-of-sample correlation with the residual
    usage only (snaps, touches)                 0.151   <- best
    projection vs price                         0.099
    projection + usage                          0.139
    everything                                  0.141
    PRICE MOVEMENT only                         0.012   <- nothing

**DraftKings' week-to-week price change predicts nothing.** That was the
original hypothesis -- that DK lags a role change and the lag shows up as a
stale price -- and it is refuted: both the dollar change and the percentage
change span zero in a tercile split (+0.013 SD, CI -0.023 to 0.049), and adding
them to the model does not improve it. Recorded here because it is the kind of
idea that looks obviously right and would otherwise be tried again.

**Last game's snap share and touches are the whole signal**, and they beat our
own projection at this job. Adding the projection to them makes the model
slightly worse, so the board deliberately does not use it.

## Where the signal actually is: CHEAP players with a real role

Ranked over the whole slate the score is worth little, and the "fade" end is
$3,000 receivers with a 2% snap share -- true, and useless, because nobody was
going to roster them. Two splits make it decision-relevant: at each position's
median salary, and **within position**.

The within-position part is not a refinement, it is a correction. Quarterbacks
all sit at 100% snap share, so a pooled ranking maxes the snap term for every one
of them and the first version of this list came back as eleven cheap
quarterbacks -- a statement about the position, not about mispricing. Everything
below takes the top or bottom fifth within each position and averages across
positions. Held out on 2024-2025, 32 slates:

    population                          effect   95% CI            points
    cheap half, top fifth              +0.205    0.123 to 0.287     +1.51
    cheap half, bottom fifth           -0.205   -0.273 to -0.137    -1.52
    dear half, top fifth               +0.033   -0.044 to 0.110     spans 0
    dear half, bottom fifth            -0.119   -0.223 to -0.015    -0.88

**DraftKings prices usage correctly at the top of the board and underprices it
at the bottom.** Among expensive players, knowing the snap share adds nothing --
the dear-half top fifth spans zero. Among cheap players it is worth **+1.51 DK
points** against the price-implied line, and the spread from the top fifth to the
bottom fifth of the cheap half is about **3 points**.

That is the answer to "how do we find the cheap players who outperform": they are
the ones who already had a real role last week.

Per position, and this is where the list is and is not supported:

    cheap half, top fifth      QB +0.217  RB +0.243  WR +0.139   TE +0.210 spans 0
    dear half, bottom fifth    RB -0.216  WR -0.095   QB and TE both span zero

So the target list stands at QB, RB and WR and is unproven at TE. **The fade list
is only supported at RB and WR**, and even pooled it barely clears zero. Treat
the target side as the finding and the fade side as suggestive.

## And it is NOT what the optimizer already does

The obvious objection is that the optimizer maximises projection against salary
and would find these players anyway. It does not. Same population, same held-out
seasons, cheap half top fifth within position:

    ranked by projection vs price (what ships)   +0.047 SD   spans zero
    ranked by usage                              +0.205 SD
    both together                                +0.129 SD

The two lists overlap **27%**. The projection finds nothing among cheap players,
usage finds 1.5 points, and combining them is worse than usage alone -- so the
board uses usage only, deliberately.

## What it is worth over the whole board

    top decile, all players       +0.142 SD  (95% CI 0.074 to 0.210)
    bottom decile, all players    -0.306 SD  (95% CI -0.367 to -0.245)

Those pool positions and are the confounded view; they are kept only for
context. Nothing here makes a lineup safe or a profit expected -- one SD is
roughly 7 DK points and a lineup is nine players against a field of thousands.

## The week-2 caveat

The features need only ONE prior game, so the board works in week 2. But only two
held-out slates are week 2, so any week-2-specific interval rests on two
observations and is not a measurement. The 32-slate numbers above are the ones to
quote. A one-game snap share is also a one-game sample: `games` is published per
player so the board can say so.

## Deliberate limits

- **No defences.** The historical workbook has no DST rows, so there is no DST
  price line to fit and DST is excluded rather than guessed at.
- **Unknown touches are not zero.** A player absent from the weekly stats
  release had no stat line. That is usually a blocking tight end and genuinely
  zero, but it is indistinguishable from a failed join, and zero touches at a
  high snap share scores as strongly overpriced. Those players are returned
  unranked with the reason stated.
- The score has no salary term, and that is correct rather than an oversight:
  the *target* is already points above price, so two players with the same usage
  have the same expected edge whatever they cost. The claim is that DK's price
  does not fully account for usage -- not that cheap players are good.
"""
from __future__ import annotations

# Fitted over 2017-2025, 35,384 player-weeks with a price of at least $3,000 and
# at least one prior game in the season. `r` is salary's own correlation with
# points -- price explains the level well and the week badly, and residSd is what
# is left for anything else to predict.
PRICE_LINE: dict[str, dict[str, float]] = {
    "QB": {"intercept": -11.3570, "slope": 4.7612, "resid_sd": 8.3519, "r": 0.4901, "n": 4989},
    "RB": {"intercept": -8.3005, "slope": 3.4954, "resid_sd": 7.1824, "r": 0.5505, "n": 11110},
    "TE": {"intercept": 0.0064, "slope": 2.3131, "resid_sd": 6.8407, "r": 0.3467, "n": 3311},
    "WR": {"intercept": -3.7705, "slope": 2.8706, "resid_sd": 7.0912, "r": 0.5269, "n": 15974},
}

# OLS on the standardised residual. Snap share is a percentage (0-100) and
# touches are carries + receptions, matching the workbook columns the fit used.
USAGE = {"intercept": -0.268841, "snap_pct": 0.003613, "touches": 0.009120}

#: Held-out performance, for display. Never quote these as expected profit.
#: Effects are in SDs of the price residual; `points` converts at 7.4 per SD,
#: the mean residual SD across positions.
MEASURED = {
    "holdout": "2024-2025",
    "slates": 32,
    "sd_to_points": 7.4,
    # The two lists the board shows. Top/bottom fifth WITHIN position.
    "cheap_top": {"sd": 0.205, "ci": (0.123, 0.287), "points": 1.51},
    "cheap_bottom": {"sd": -0.205, "ci": (-0.273, -0.137), "points": -1.52},
    "dear_top": {"sd": 0.033, "ci": (-0.044, 0.110), "points": 0.24},
    "dear_bottom": {"sd": -0.119, "ci": (-0.223, -0.015), "points": -0.88},
    # Per position. None of these is a separate discovery -- they are the same
    # measurement on subsets, so do not add them up.
    "cheap_top_by_position": {
        "QB": {"sd": 0.217, "ci": (0.099, 0.335)},
        "RB": {"sd": 0.243, "ci": (0.120, 0.366)},
        "WR": {"sd": 0.139, "ci": (0.049, 0.229)},
        "TE": {"sd": 0.210, "ci": (-0.037, 0.456)},          # spans zero
    },
    "dear_bottom_by_position": {
        "RB": {"sd": -0.216, "ci": (-0.374, -0.059)},
        "WR": {"sd": -0.095, "ci": (-0.182, -0.007)},
        "QB": {"sd": -0.125, "ci": (-0.372, 0.122)},         # spans zero
        "TE": {"sd": 0.014, "ci": (-0.369, 0.398)},          # spans zero
    },
    # The comparison that justifies shipping it at all.
    "projection_cheap_top": {"sd": 0.047, "ci": (-0.033, 0.126)},
    "overlap_with_projection": 0.27,
    # Whole-board figures, for context.
    "top_decile_sd": 0.142, "top_decile_ci": (0.074, 0.210),
    "bottom_decile_sd": -0.306, "bottom_decile_ci": (-0.367, -0.245),
    "correlation": 0.161,
    # Refuted: DraftKings' own week-to-week price change.
    "price_movement_sd": 0.013, "price_movement_ci": (-0.023, 0.049),
}

MIN_SALARY = 3000


def _field(record: dict, *names):
    """
    Read a field under either spelling.

    `usage-2026.json` is written by the TypeScript build, which spells keys in
    camelCase (`snapPct`), while this module is Python. Accepting both means a
    rename on either side degrades to a missing value rather than a KeyError
    halfway down a board that has already rendered.
    """
    for n in names:
        if n in record and record[n] is not None:
            return record[n]
    return None


def price_implied(position: str, salary: float) -> float | None:
    """Points the price implies, or None for a position with no fitted line."""
    line = PRICE_LINE.get((position or "").upper())
    if not line or not salary:
        return None
    return line["intercept"] + line["slope"] * (salary / 1000.0)


def residual_sd(position: str) -> float | None:
    line = PRICE_LINE.get((position or "").upper())
    return line["resid_sd"] if line else None


def edge_sd(position: str, snap_pct: float, touches: float) -> float | None:
    """
    Expected points above price, in SDs of the residual.

    Returns None when the position has no price line, so a missing fit can never
    silently read as an edge of zero.
    """
    if (position or "").upper() not in PRICE_LINE:
        return None
    return (USAGE["intercept"]
            + USAGE["snap_pct"] * float(snap_pct)
            + USAGE["touches"] * float(touches))


def edge_points(position: str, snap_pct: float, touches: float) -> float | None:
    """The same edge in DK points, which is what a lineup is scored in."""
    sd = edge_sd(position, snap_pct, touches)
    rsd = residual_sd(position)
    return None if sd is None or rsd is None else sd * rsd


def board(rows: list[dict], usage: dict | None, min_salary: int = MIN_SALARY) -> dict:
    """
    Score a slate.

    `rows` are pool rows (name, position, team, salary, projection). `usage` is
    the published `usage-<season>.json`. Returns ranked players plus everyone who
    could not be ranked and why, because a board that silently drops players is
    the failure mode this project keeps catching.
    """
    from .blend import key as name_key

    players = (usage or {}).get("players") or {}
    ranked: list[dict] = []
    unranked: list[dict] = []

    for r in rows:
        pos = (r.get("position") or "").upper()
        salary = float(r.get("salary") or 0)
        row = {"name": r.get("name"), "position": pos, "team": r.get("team"),
               "salary": salary, "projection": r.get("projection")}

        if pos not in PRICE_LINE:
            unranked.append({**row, "why": f"no fitted price line for {pos or 'this position'}"
                                            + (" (the workbook has no DST rows)" if pos == "DST" else "")})
            continue
        if salary < min_salary:
            unranked.append({**row, "why": f"priced below ${min_salary:,}, outside the fit"})
            continue

        u = players.get(name_key(r.get("name") or ""))
        if not u:
            unranked.append({**row, "why": "no snap-count record this season"})
            continue
        snap = _field(u, "snapPct", "snap_pct")
        touches = _field(u, "touches")
        if snap is None:
            unranked.append({**row, "why": "snap-count record carries no snap share"})
            continue
        if touches is None:
            unranked.append({**row, "why": "played, but no stat line — touches unknown, not zero"})
            continue

        implied = price_implied(pos, salary)
        pts = edge_points(pos, snap, touches)
        ranked.append({
            **row,
            "price_implied": implied,
            "snap_pct": snap,
            "touches": touches,
            "games": u.get("games", 0),
            "from_week": _field(u, "week"),
            "edge_sd": edge_sd(pos, snap, touches),
            "edge_points": pts,
            "verdict": "underpriced" if (pts or 0) > 0 else "overpriced",
        })

    ranked.sort(key=lambda x: x["edge_points"], reverse=True)

    # Cheap means at or below the position's median salary ON THIS SLATE, which
    # is how the measurement defined it. Splitting on a fixed dollar figure
    # instead would drift as DraftKings' pricing moves across a season.
    for pos in {r["position"] for r in ranked}:
        peers = sorted(r["salary"] for r in ranked if r["position"] == pos)
        median = peers[len(peers) // 2]
        for r in ranked:
            if r["position"] == pos:
                r["cheap"] = r["salary"] <= median
                r["position_median"] = median

    cheap = [r for r in ranked if r["cheap"]]
    dear = [r for r in ranked if not r["cheap"]]

    def by_position(pool, end):
        """
        Top or bottom fifth WITHIN each position, as the measurement did.

        Selecting across positions instead lets quarterbacks -- every one of them
        at 100% snap share -- fill the whole target list, which is what the first
        version of this did.
        """
        out = []
        for pos in sorted({r["position"] for r in pool}):
            peers = [r for r in pool if r["position"] == pos]
            peers.sort(key=lambda x: x["edge_points"], reverse=(end == "top"))
            out.extend(peers[:max(1, len(peers) // 5)])
        out.sort(key=lambda x: x["edge_points"], reverse=(end == "top"))
        return out

    # Positions where the held-out interval actually cleared zero. A player from
    # any other position is still listed, flagged, so the list cannot quietly
    # borrow support it does not have.
    target_support = {p for p, v in MEASURED["cheap_top_by_position"].items() if v["ci"][0] > 0}
    fade_support = {p for p, v in MEASURED["dear_bottom_by_position"].items() if v["ci"][1] < 0}

    # A player selected by the within-position rule but whose edge has the wrong
    # sign is dropped. Every quarterback on this slate carries a positive edge,
    # so the bottom fifth of the dear half included two of them -- and listing a
    # player under "overpriced" while the model says he beats his price is a
    # contradiction, however the selection rule got there.
    targets = [r for r in by_position(cheap, "top") if r["edge_points"] > 0]
    fades = [r for r in by_position(dear, "bottom") if r["edge_points"] < 0]
    for r in targets:
        r["measured"] = r["position"] in target_support
    for r in fades:
        r["measured"] = r["position"] in fade_support

    return {
        "ranked": ranked,
        "unranked": unranked,
        # The two lists with held-out support. Targets come from the cheap half
        # only, because among expensive players this signal measured nothing.
        "targets": targets,
        "fades": fades,
        "cheap": cheap,
        "dear": dear,
        "target_support": sorted(target_support),
        "fade_support": sorted(fade_support),
        "through_week": (usage or {}).get("through_week") or (usage or {}).get("throughWeek"),
        "built": (usage or {}).get("built"),
    }

"""
What the Kalshi prop markets imply, in DraftKings points.

## It feeds NOTHING. That is the current state, not an oversight.

No historical Kalshi data exists -- settled markets have empty books, so a week
not snapshotted before kickoff is gone for good. With no history there is
nothing to backtest against, and the project standard is that nothing becomes a
default until it shows held-out value. So these numbers are shown and not used.
`scripts/snapshot-kalshi.mjs` is building the archive that would eventually make
a test possible; until then this tab is a window, not an input.

## Turning a ladder into an expectation

Each market is "will this player exceed N?", so a ladder of strikes gives the
survival function S(x) = P(stat >= x) at each strike. For a non-negative
quantity the mean is the area under it:

    E[X] = integral of S(x) dx, from 0 upward

which is approximated by trapezoids between adjacent strikes. Two edges need a
decision and both are stated rather than hidden:

  - BELOW the lowest strike, S is assumed to run linearly from 1 down to
    S(s1). That is conservative for a low first strike and crude for a high
    one, so `floorSpan` reports how much of the total came from that segment.
  - ABOVE the highest strike, the tail is assumed exponential with the decay
    implied by the last two rungs, which is milder than truncating at zero and
    less aggressive than assuming the last gap repeats forever.

A mid price is not a probability. The book is thin, many rungs have zero volume,
and the spread is real money -- the file's own warning says so. Treating the
midpoint as P is the standard convenience and it is a convenience.

## The DK points it produces are PARTIAL, and this week badly so

`dkPointsPerUnit` converts each stat to DraftKings points, but the ladders cover
only yardage and receptions. **The touchdown markets returned no players at all
on the 2026-09-27 capture**, and touchdowns are 4 points for a pass and 6 for a
rush or catch. A quarterback throwing three is 12 points this cannot see.

So a Kalshi total will read LOW against a real projection, always, and the gap
is not a disagreement -- it is a missing term. `covered` lists which stats a
player actually had a market for, so a reader can tell a genuine difference from
an absent one.
"""
from __future__ import annotations

import math

#: Stats whose absence makes a total badly incomplete rather than slightly.
SCORING_STATS = {"pass_td", "any_td"}


def implied_mean(rungs: list[dict]) -> tuple[float, float] | None:
    """
    Market-implied mean of one stat, and the share of it below the first strike.

    Returns None for a ladder too short or too incoherent to read. A survival
    function must be non-increasing; a thin book sometimes quotes one that is
    not, and reordering it would invent a coherence the market did not have.
    """
    pts = sorted(((float(r["strike"]), float(r["mid"])) for r in rungs
                  if r.get("mid") is not None), key=lambda t: t[0])
    if len(pts) < 2:
        return None
    for (_, a), (_, b) in zip(pts, pts[1:]):
        if b > a + 0.02:                       # 2c of slack for a crossed book
            return None

    first_strike, first_p = pts[0]
    # Below the first strike: S runs from 1 down to S(s1), linearly.
    below = first_strike * (1.0 + first_p) / 2.0
    total = below

    for (s0, p0), (s1, p1) in zip(pts, pts[1:]):
        total += (p0 + p1) / 2.0 * (s1 - s0)

    # Above the last strike: exponential decay at the rate the last two rungs
    # imply -- but BOUNDED, because that rate can be ~0 and the integral of a
    # flat tail is infinite.
    #
    # Jerry Jeudy's Week 2 receiving ladder quoted 0.055 at both 80 and 90
    # yards. In floating point the first is 0.05500000000000001, so the ratio
    # came to 1.0000000000000002, lambda to 2.2e-18, and the tail to 2.5e15
    # yards. The pooled MAE read 732 billion, which is at least loud; a slightly
    # less flat ladder would have produced a merely wrong number instead.
    #
    # So the tail can never exceed what one more full strike-step of decay would
    # give, twice over. A market that has stopped discriminating at the top of
    # its ladder is telling you it does not know, and the honest response is a
    # bounded guess rather than an unbounded one.
    (s_prev, p_prev), (s_last, p_last) = pts[-2], pts[-1]
    gap = s_last - s_prev
    if p_last > 0 and gap > 0:
        tail_cap = p_last * gap * 2.0
        tail = tail_cap
        if p_prev > p_last * 1.02:                    # a real decay, not FP noise
            lam = math.log(p_prev / p_last) / gap
            if lam > 0:
                tail = min(p_last / lam, tail_cap)
        total += tail
    return total, (below / total if total > 0 else 0.0)


def player_rows(props: dict | None) -> dict[str, dict]:
    """
    One row per player: implied DK points, and which stats it could see.

    Keyed by the player's name exactly as Kalshi spells it; the caller joins.
    Receptions and receiving yards are separate markets and both count. Rushing
    and receiving yards are separate too, but `scrimmage_yards` would double
    count against them, so it is only used when neither component is present.
    """
    out: dict[str, dict] = {}
    if not props:
        return out
    series = (props.get("series") or {})

    for meta in series.values():
        stat = meta.get("stat")
        per_unit = float(meta.get("dkPointsPerUnit") or 0)
        for entry in (meta.get("players") or []):
            name = entry.get("player")
            if not name:
                continue
            got = implied_mean(entry.get("rungs") or [])
            if got is None:
                continue
            mean, floor_span = got
            row = out.setdefault(name, {"name": name, "game": entry.get("game"),
                                        "stats": {}, "points": 0.0, "covered": [],
                                        "floorSpan": 0.0, "rungs": 0})
            rungs = entry.get("rungs") or []
            vol = sum(float(r.get("volume") or 0) for r in rungs)
            row["stats"][stat] = {"mean": mean, "points": mean * per_unit,
                                  "floorSpan": floor_span, "rungs": len(rungs),
                                  "volume": vol,
                                  "tradedRungs": sum(1 for r in rungs
                                                     if float(r.get("volume") or 0) > 0)}
            row["rungs"] += len(rungs)

    for row in out.values():
        s = row["stats"]
        use = [k for k in s if k != "scrimmage_yards"]
        if "scrimmage_yards" in s and not ({"rush_yards", "rec_yards"} & set(s)):
            use.append("scrimmage_yards")
        row["points"] = sum(s[k]["points"] for k in use)
        row["covered"] = sorted(use)
        row["missingScoring"] = sorted(SCORING_STATS - set(s))
        row["floorSpan"] = (max(s[k]["floorSpan"] for k in use) if use else 0.0)
        # Volume is the difference between a price and a quote. The file's own
        # warning says so: a wide spread with no volume is one market maker's
        # guess, not anyone's opinion backed by money. 26 of 373 player-markets
        # had no trades at all on the Week 3 capture.
        row["volume"] = sum(s[k].get("volume", 0.0) for k in use)
        row["tradedRungs"] = sum(s[k].get("tradedRungs", 0) for k in use)
    return out


def depth_rank(pool: list[dict]) -> dict[str, int]:
    """
    Where each player sits on his own team's depth chart, by salary.

    WR1 is the most expensive receiver on his team, WR2 the next, and so on.
    That is what "WR2/WR3" means -- a role, not a price band. Ranking by salary
    across the whole slate instead would call a cheap team's WR1 a WR3, which is
    the opposite of the intent.
    """
    groups: dict[tuple, list[dict]] = {}
    for p in pool:
        groups.setdefault((p.get("team"), p.get("position")), []).append(p)
    rank = {}
    for members in groups.values():
        for i, p in enumerate(sorted(members, key=lambda x: -float(x.get("salary") or 0)), 1):
            rank[p["id"]] = i
    return rank


def join_to_pool(pool: list[dict], props: dict | None, key) -> list[dict]:
    """
    Attach the market view to pool rows, leaving the pool's own numbers alone.

    Nothing here modifies `projection`, `ceiling` or `salary`. If that ever
    changes it must go through a held-out test first, and this docstring is the
    place the change will be argued.
    """
    rows = player_rows(props)
    by_key = {key(n): r for n, r in rows.items()}
    out = []
    for p in pool:
        r = by_key.get(key(p["name"]))
        out.append({**p,
                    "kalshi_points": (r or {}).get("points"),
                    "kalshi_covered": (r or {}).get("covered") or [],
                    "kalshi_missing": (r or {}).get("missingScoring") or [],
                    "kalshi_rungs": (r or {}).get("rungs") or 0,
                    "kalshi_floor_span": (r or {}).get("floorSpan")})
    return out


#: The watchlist's live record. This is the number that matters and it is bad.
#: Logged before kickoff each week, graded after; see
#: app/scripts/kalshi_watchlist.py and app/evidence/kalshi-watchlist.csv.
WATCHLIST_RECORD = {
    "weeks": 3, "graded": 84, "popped": 2, "hit_rate": 0.024, "base_rate": 0.08,
    "by_gap": {"over 3 pts": (0, 19), "2 to 3": (0, 16), "1 to 2": (1, 24), "under 1": (1, 25)},
    "top5_per_week": (0, 10),
    "note": ("Worse than picking at random, and monotone the WRONG way -- the bigger the "
             "disagreement, the worse the outcome. 84 picks with 2 poppers cannot rule out the "
             "8% base rate on its own, but there is no positive evidence here at all and every "
             "cut points the same direction."),
}

#: What the first forward validation found, for display. Two weeks is not a
#: measurement; it is the first two data points of one.
MEASURED = {
    "weeks": 2,
    "players": 338,
    "target": "non-touchdown DK points, which is what the ladders price",
    "kalshi_mae": 4.52, "kalshi_corr": 0.386,
    "dk_mae": 4.98, "dk_corr": 0.299,
    "note": ("DraftKings' AvgPointsPerGame includes touchdowns, so it is rescaled by the "
             "non-TD share of points (0.717, calibrated on Week 1) before comparison -- "
             "otherwise it is scored against a target it was never aimed at and Kalshi wins "
             "by construction. Unadjusted it reads 6.17 and the gap looks three times larger "
             "than it is."),
}


#: A market with less volume than this is a quote, not a price. Set at the
#: median traded volume across player-markets on the 2026-09-27 capture, so
#: roughly half of all markets clear it. It is a judgement, not a measurement.
MIN_VOLUME = 400.0

#: Depth-chart slots that are the target: the second and third options on their
#: own team, where a cheap price and a real role can coexist. WR1s are priced
#: for what they do and WR4s do not play enough.
TARGET_RANKS = (2, 3)


def watchlist(pool: list[dict], props: dict | None, key,
              positions=("WR", "RB"), ranks=TARGET_RANKS,
              min_volume: float = MIN_VOLUME, td_share: float = 0.717) -> list[dict]:
    """
    WR2/WR3 and RB2/RB3 where real money disagrees with our projection.

    Three filters, each for a stated reason:

      depth rank   second or third on his own team. That is the slot where a
                   tournament is won -- cheap enough to afford, involved enough
                   to explode.
      volume       the market must have traded. A wide spread with no volume is
                   one market maker's guess.
      coverage     the market must have priced what the player actually does --
                   receiving for a receiver, rushing for a back. Kalshi priced
                   Aaron Rodgers' RUSHING yards and nothing else, which read as
                   the market valuing him at 0.7 points.

    `gap` is Kalshi minus our projection with touchdowns stripped out of ours,
    since the ladders do not price them. A positive gap means the market expects
    more than we do. Sorted by that, largest first.
    """
    need = {"WR": {"rec_yards", "receptions"}, "TE": {"rec_yards", "receptions"},
            "RB": {"rush_yards"}, "QB": {"pass_yards"}}
    rank = depth_rank(pool)
    rows = player_rows(props)
    by_key = {key(n): r for n, r in rows.items()}

    out = []
    for p in pool:
        pos = p.get("position")
        if pos not in positions or rank.get(p["id"]) not in ranks:
            continue
        m = by_key.get(key(p["name"]))
        if not m or not need.get(pos, set()).issubset(set(m["covered"])):
            continue
        if m["volume"] < min_volume:
            continue
        ours = float(p.get("projection") or 0) * td_share
        out.append({
            "id": p["id"], "name": p["name"], "position": pos, "team": p.get("team"),
            "opponent": p.get("opponent"), "salary": float(p.get("salary") or 0),
            "depth": f'{pos}{rank[p["id"]]}',
            "ours_noTD": round(ours, 2),
            "kalshi": round(m["points"], 2),
            "gap": round(m["points"] - ours, 2),
            "volume": round(m["volume"]),
            "traded_rungs": m["tradedRungs"],
            "priced": ",".join(m["covered"]),
        })
    out.sort(key=lambda r: -r["gap"])
    return out

"""
Lineup construction.

Stacks are built CONSTRUCTIVELY -- the quarterback and his pass catchers are
chosen first and the roster filled around them. Sampling ordinary lineups and
keeping the ones that happen to be stacked produces 42 usable candidates from
24,000, which measures starvation rather than stacking.
"""
from __future__ import annotations
import random as _random
from .rules import (EXPOSURE_PCT, FLEX_POSITIONS, MAX_PLAYERS_PER_TEAM, MAX_SHARED_PLAYERS,
                    MIN_DISTINCT_GAMES, POSITION_MIN, QB_EXPOSURE_PCT, ROSTER_SIZE,
                    SALARY_CAP, STACK_BRING_BACK, STACK_MATES)

CATCHERS = ("WR", "TE")


def top_receiver(team: str, players: list[dict]) -> dict | None:
    """
    The team's WR1, taken as its highest-salaried receiver.

    Salary is used rather than projected points because salary IS the market's
    view of the depth chart, set with current role information, while the
    projection here is last season's average and cannot tell a promoted
    receiver from a demoted one.
    """
    wrs = [p for p in players if p["position"] == "WR" and p["team"] == team]
    return max(wrs, key=lambda p: (p["salary"], p["projection"])) if wrs else None


def _maxima(flex_allowed) -> dict[str, int]:
    """
    Position maxima derived from the FLEX rule.

    Hard-coding TE at 2 was a bug: restricting the FLEX step alone changed
    nothing, because the stack step runs first and counts tight ends as pass
    catchers, so a stack could take two before the FLEX was considered.
    """
    return {
        "QB": 1, "DST": 1,
        "RB": POSITION_MIN["RB"] + (1 if "RB" in flex_allowed else 0),
        "WR": POSITION_MIN["WR"] + (1 if "WR" in flex_allowed else 0),
        "TE": POSITION_MIN["TE"] + (1 if "TE" in flex_allowed else 0),
    }


def _weight(p: dict, ceiling_weight: float) -> float:
    """
    Sampling weight: points per $1,000, sharpened so the better values dominate
    without ever being certain. The floor matters -- a defence can carry a
    negative average, and a negative base raised to a fractional power is not a
    real number, so the base is clamped before the exponent rather than after.
    """
    value = p["projection"] + ceiling_weight * 0.07 * (p["ceiling"] - p["projection"])
    per_thousand = max(0.05, value / max(1.0, p["salary"] / 1000))
    return per_thousand ** 3.2


class Index:
    """
    Everything about the pool that does not change during a build, computed once.

    Without this, each of the nine picks rescanned all ~500 players and
    recomputed a fractional power per player -- roughly 20 seconds for a
    ten-lineup portfolio, which is not an interactive page. The sampling is
    unchanged; only the bookkeeping moved.
    """

    def __init__(self, players: list[dict], ceiling_weight: float):
        self.weight = {p["id"]: _weight(p, ceiling_weight) for p in players}
        self.by_pos: dict[str, list[dict]] = {}
        self.catchers: dict[str, list[dict]] = {}
        for p in players:
            self.by_pos.setdefault(p["position"], []).append(p)
            if p["position"] in CATCHERS:
                self.catchers.setdefault(p["team"], []).append(p)
        for group in self.by_pos.values():
            group.sort(key=lambda p: self.weight[p["id"]], reverse=True)
        teams = {p["team"] for p in players}
        self.top_wr = {t: top_receiver(t, players) for t in teams}


def _sample(pool, rng, index: "Index") -> dict | None:
    if not pool:
        return None
    total = 0.0
    for p in pool:
        total += index.weight[p["id"]]
    cursor = rng.random() * total
    for p in pool:
        cursor -= index.weight[p["id"]]
        if cursor <= 0:
            return p
    return pool[-1]


def build_lineup(players: list[dict], rng, ceiling_weight: float = 0.25,
                 mates: int = STACK_MATES, bring_back: int = STACK_BRING_BACK,
                 flex_allowed=FLEX_POSITIONS, require: set[str] | None = None,
                 index: "Index | None" = None, require_wr1: bool = False) -> list[dict] | None:
    """
    One stacked lineup, or None when this attempt could not be completed.

    `require` seeds the roster with specific players. It is for building a
    single lineup around someone on purpose -- it is NOT the must-play feature,
    which means "somewhere in the portfolio" and is handled by ensure_included.
    Conflating the two put a must-play quarterback in all ten lineups.
    """
    require = require or set()
    index = index or Index(players, ceiling_weight)
    MAX = _maxima(flex_allowed)
    chosen: list[dict] = []
    ids: set[str] = set()

    def take(p):
        if p is None or p["id"] in ids:
            return False
        chosen.append(p); ids.add(p["id"]); return True

    def count(pos):
        return sum(1 for p in chosen if p["position"] == pos)

    for p in players:
        if p["id"] in require:
            take(p)
    if len(chosen) > ROSTER_SIZE:
        return None

    qb = next((p for p in chosen if p["position"] == "QB"), None)
    if qb is None:
        qb = _sample([p for p in index.by_pos.get("QB", []) if p["id"] not in ids], rng, index)
        if not take(qb):
            return None

    # The WR1 goes in first when required, so the remaining mates are drawn
    # around him rather than competing with him for the same slots.
    if require_wr1 and mates:
        wr1 = index.top_wr.get(qb["team"])
        if wr1 is None:
            return None
        if wr1["id"] not in ids:
            if count("WR") >= MAX["WR"] or not take(wr1):
                return None
        mates -= 1

    for _ in range(mates):
        pool = [p for p in index.catchers.get(qb["team"], [])
                if p["id"] not in ids and count(p["position"]) < MAX[p["position"]]]
        if not take(_sample(pool, rng, index)):
            return None
    for _ in range(bring_back):
        pool = [p for p in index.catchers.get(qb["opponent"], [])
                if p["id"] not in ids and count(p["position"]) < MAX[p["position"]]]
        if not take(_sample(pool, rng, index)):
            return None

    if count("DST") == 0:
        pool = [p for p in index.by_pos.get("DST", []) if p["id"] not in ids]
        if not take(_sample(pool, rng, index)):
            return None

    def fillable(pos):
        if count(pos) >= MAX[pos]:
            return []
        per_team: dict[str, int] = {}
        for c in chosen:
            per_team[c["team"]] = per_team.get(c["team"], 0) + 1
        return [p for p in index.by_pos.get(pos, [])
                if p["id"] not in ids
                and per_team.get(p["team"], 0) < MAX_PLAYERS_PER_TEAM]

    for pos in ("RB", "WR", "TE"):
        while count(pos) < POSITION_MIN[pos]:
            if not take(_sample(fillable(pos), rng, index)):
                return None
    while len(chosen) < ROSTER_SIZE:
        pool = [p for pos in flex_allowed for p in fillable(pos)]
        if not take(_sample(pool, rng, index)):
            return None

    if len(chosen) != ROSTER_SIZE:
        return None
    if sum(p["salary"] for p in chosen) > SALARY_CAP:
        return None
    if len({p["game"] for p in chosen}) < MIN_DISTINCT_GAMES:
        return None
    if max((sum(1 for p in chosen if p["team"] == t) for t in {p["team"] for p in chosen}), default=0) > MAX_PLAYERS_PER_TEAM:
        return None
    return chosen


def projection(lineup) -> float:
    return sum(p["projection"] for p in lineup)


def build_portfolio(players: list[dict], count: int = 10, seed: int = 1,
                    ceiling_weight: float = 0.25, attempts: int = 20000,
                    must_play: set[str] | None = None,
                    qb_exposure: int = QB_EXPOSURE_PCT,
                    rb_exposure: int = EXPOSURE_PCT,
                    other_exposure: int | None = None,
                    require_wr1: bool = False) -> list[list[dict]]:
    """
    A portfolio of distinct, diversified lineups.

    Exposure is capped per position, with the quarterback tighter than the rest
    -- a slate offers a dozen viable quarterbacks and a hundred receivers, so a
    single percentage cap never binds where concentration hurts most.
    """
    rng = _random.Random(seed)
    index = Index(players, ceiling_weight)
    seen: dict[tuple, list[dict]] = {}
    for _ in range(attempts):
        l = build_lineup(players, rng, ceiling_weight, index=index, require_wr1=require_wr1)
        if not l:
            continue
        key = tuple(sorted(p["id"] for p in l))
        if key not in seen or projection(seen[key]) < projection(l):
            seen[key] = l
    pool = sorted(seen.values(), key=projection, reverse=True)

    caps = {"QB": max(1, count * qb_exposure // 100),
            "RB": max(1, count * rb_exposure // 100)}
    # WR, TE and DST. They used to fall through to EXPOSURE_PCT at 75%, which at
    # ten lineups is seven -- and a lineup holds three receivers plus usually
    # the FLEX, so that is where an uncapped default bites hardest.
    other = other_exposure if other_exposure is not None else EXPOSURE_PCT
    default_cap = max(1, count * other // 100)
    selected: list[list[dict]] = []
    uses: dict[str, int] = {}
    for l in pool:
        if len(selected) == count:
            break
        if any(len({p["id"] for p in l} & {q["id"] for q in s}) > MAX_SHARED_PLAYERS for s in selected):
            continue
        if any(uses.get(p["id"], 0) >= caps.get(p["position"], default_cap) for p in l):
            continue
        selected.append(l)
        for p in l:
            uses[p["id"]] = uses.get(p["id"], 0) + 1

    if must_play:
        selected = ensure_included(selected, players, must_play, rng, ceiling_weight,
                                   index, require_wr1)
    return selected


def ensure_included(lineups: list[list[dict]], players: list[dict], required,
                    rng, ceiling_weight: float = 0.25,
                    index: "Index | None" = None,
                    require_wr1: bool = False) -> list[list[dict]]:
    """
    Put each must-play player in AT LEAST ONE lineup -- not in all of them.

    Rebuilds a single lineup around the missing player rather than swapping him
    in, because a swap at quarterback would leave the stack pointing at the old
    quarterback's team. The lineup sacrificed is the weakest one that does not
    already carry another must-play.

    A player who cannot be placed is left unplaced and reported, rather than
    silently dropped or forced in by relaxing a roster rule.
    """
    index = index or Index(players, ceiling_weight)
    out = list(lineups)
    for pid in required:
        if any(pid in {p["id"] for p in l} for l in out):
            continue
        best = None
        for _ in range(600):
            cand = build_lineup(players, rng, ceiling_weight, require={pid}, index=index,
                                require_wr1=require_wr1)
            if cand and (best is None or projection(cand) > projection(best)):
                best = cand
        if best is None:
            continue
        protected = set(required) - {pid}
        for i in sorted(range(len(out)), key=lambda i: projection(out[i])):
            if protected & {p["id"] for p in out[i]}:
                continue
            trial = out[:i] + [best] + out[i + 1:]
            if len({tuple(sorted(p["id"] for p in l)) for l in trial}) == len(trial):
                out = trial
                break
    return out

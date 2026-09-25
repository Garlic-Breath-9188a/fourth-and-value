"""
Which contests to enter, and how many of each.

`fv/entry.py` ranks on rake and fills the cheapest thing that divides the
budget. That produced ten entries in one contest, and before the payout curves
arrived it produced ten entries in a double-up. This scores on everything this
project has actually measured instead.

## What goes into the score, and what does not

Each term is here because it was MEASURED, not because it sounds right. The
weights are a judgement about relative importance and are stated as such -- they
have not been fitted to anything, because there is no outcome data to fit them
to. Two weeks of results cannot calibrate five weights.

    rake            The one free lever. $3-$5 contests keep 15.0% of every
                    dollar and $100+ keep 9.7% (Spearman 0.86, n=100). Adopted.
    top-heaviness   Stacking does not raise the mean -- it moves P(180+) from
                    0.13% to 0.25% and leaves the average flat. You are paying
                    variance to buy the top of the curve, so the top of the
                    curve has to pay. A contest topping out at 14x the buy-in
                    cannot repay a tournament build.
    single-entry    Measured as the softest room: single-entry GPPs average
                    12.7% rake against 13.7%, and nobody can field 150 lineups
                    against your one.
    overlay         Arithmetic, not an edge: a guaranteed pool short of capacity
                    is the house topping it up. Weighted lightly and decays,
                    because entry counts move hard in the last hours -- the Week
                    1 contests went from 35-58% full to 58-92% in a day.

Deliberately NOT scored: expected ROI. That needs the field model, which still
scores about ten points below a real field even with perfect ownership and
correct stacking behaviour. A number built on it would be confident and wrong.

## The honest frame

None of this is an edge. It makes the hole shallower. An account with no edge
loses rake, and every contest here keeps 14-15%.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .shape import playable_shape

# Weights. Stated, not fitted -- there is no outcome data to fit them against.
W_RAKE = 1.00
W_TOP = 0.55
W_SINGLE = 0.35
W_OVERLAY = 0.20

# A contest needs a top prize worth chasing AND a believable field.
MIN_FIELD = 200


@dataclass
class Scored:
    contest: dict
    score: float
    rake: float
    reasons: list[str] = field(default_factory=list)


def rake_of(c: dict) -> float | None:
    cap = (c.get("maxEntries") or 0) * (c.get("entryFee") or 0)
    if not cap:
        return None
    return (cap - c.get("totalPrizes", 0)) / cap


def overlay_of(c: dict) -> float:
    """Share of the prize pool the field has not paid for, at the snapshot."""
    pool = c.get("totalPrizes", 0) or 0
    taken = (c.get("entered") or 0) * (c.get("entryFee") or 0)
    return max(0.0, (pool - taken) / pool) if pool else 0.0


def score(c: dict) -> Scored | None:
    """None when the contest should not be entered at all."""
    if not playable_shape(c.get("structure", "unknown")):
        return None
    if (c.get("maxEntries") or 0) < MIN_FIELD:
        return None
    r = rake_of(c)
    if r is None:
        return None

    reasons = []
    # Rake, mapped so 10% scores 1.0 and 16% scores 0. The band is the one the
    # lobby actually spans; outside it the term saturates rather than inverting.
    s_rake = max(0.0, min(1.0, (0.16 - r) / 0.06))
    reasons.append(f"{r * 100:.1f}% rake")

    top = c.get("topPrizeMultiple")
    if top is None:
        # Unknown top prize is scored as merely acceptable, never as good. The
        # $20 100-Player looked excellent on every known column and topped out
        # at 14x.
        s_top = 0.35
        reasons.append("top prize unknown")
    else:
        s_top = max(0.0, min(1.0, (top - 50) / 950))
        reasons.append(f"{top:,.0f}x top prize")

    s_single = 1.0 if c.get("maxEntriesPerUser") == 1 else 0.0
    if s_single:
        reasons.append("single-entry")

    ov = overlay_of(c)
    s_ov = min(1.0, ov / 0.5)
    if ov > 0.05:
        reasons.append(f"{ov * 100:.0f}% overlay at capture")

    total = W_RAKE * s_rake + W_TOP * s_top + W_SINGLE * s_single + W_OVERLAY * s_ov
    return Scored(contest=c, score=round(total, 4), rake=r, reasons=reasons)


#: No single entry may take more than this share of the week's budget.
#: The entry planner once put $27 of a $40 budget on one seat, which is how a
#: week ends up riding on a single lineup. Quality-first selection walks
#: straight back into it -- the best contest on a board is often the dearest --
#: so the cap is applied to the SELECTION, not bolted on afterwards.
MAX_SEAT_SHARE = 0.5


def build(contests: list[dict], budget: float, lineups: int,
          window: str = "main",
          max_seat_share: float = MAX_SEAT_SHARE) -> tuple[list[dict], list[str]]:
    """
    Pick contests for a budget, best first, one lineup per entry.

    Spreads across DIFFERENT contests before repeating one. Entering the same
    contest ten times concentrates the whole week on a single field and payout
    curve; the project measured that portfolio diversity is load-bearing for
    best-of-N, and the same argument applies across contests.
    """
    # Boards captured before the window field existed have no such marking, so
    # the filter is skipped rather than silently emptying the board -- the same
    # rule `entry.playable` follows, and a test pins it.
    has_windows = any("window" in c for c in contests)
    eligible = [c for c in contests if not has_windows or c.get("window") == window]

    seat_cap = budget * max_seat_share
    affordable = [c for c in eligible if c["entryFee"] <= seat_cap]
    capped = len(eligible) - len(affordable)
    # If the cap leaves nothing, the budget is smaller than one good seat. Fall
    # back to the cheapest single entry rather than reporting an empty board.
    pool = [s for s in (score(c) for c in (affordable or eligible)) if s]
    pool.sort(key=lambda s: -s.score)
    if not pool:
        return [], ["No contest on this slate has a known, playable payout shape."]
    notes_head = []
    if capped:
        notes_head.append(
            f"{capped} contest(s) cost more than ${seat_cap:,.0f}, half the budget, and were "
            f"skipped so one seat cannot carry the week.")

    placed, notes = [], list(notes_head)
    spent, used = 0.0, {}
    # Pass one: the best contests, one entry each.
    for s in pool:
        if len(placed) >= lineups:
            break
        fee = s.contest["entryFee"]
        if spent + fee > budget:
            continue
        placed.append({"contest": s.contest, "fee": fee, "score": s.score,
                       "why": "; ".join(s.reasons)})
        used[s.contest["id"]] = 1
        spent += fee
    # Pass two: top up the best ones we are allowed to re-enter.
    changed = True
    while len(placed) < lineups and changed:
        changed = False
        for s in pool:
            if len(placed) >= lineups:
                break
            cid = s.contest["id"]
            cap = s.contest.get("maxEntriesPerUser") or 1
            fee = s.contest["entryFee"]
            if used.get(cid, 0) >= cap or spent + fee > budget:
                continue
            placed.append({"contest": s.contest, "fee": fee, "score": s.score,
                           "why": "; ".join(s.reasons)})
            used[cid] = used.get(cid, 0) + 1
            spent += fee
            changed = True

    if len(placed) < lineups:
        notes.append(f"Only {len(placed)} of {lineups} lineups placed; "
                     f"${budget - spent:,.0f} of the budget is unspent because "
                     "nothing else on the board has a known playable shape at a "
                     "price that fits.")
    return placed, notes

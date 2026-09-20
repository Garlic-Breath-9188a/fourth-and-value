"""
Classify a contest by its PAYOUT SHAPE, not its name and not its rake.

Two contests on the Week 2 board, side by side:

    NFL $20 50-50!                   $20, 100 max, $1,800 pool, 10.0% rake
    NFL $20 200-Player (Top 3 Win)   $20, 200 max, $3,600 pool, 10.0% rake

Identical entry fee. Identical rake. The first pays HALF the field at 1.8x the
buy-in. The second pays three people out of two hundred at 36x. One is a cash
game, the other is a lottery, and every column the lobby shows says they are the
same product.

That is why the planner cannot rank on rake alone. It had been doing exactly
that, and it wanted to put an entire $100 budget into whichever small-field game
kept the least -- which on this board is a double-up.

The name does not save you either. `booster_shaped()` catches "Booster" and
"Double Up" because DraftKings names those consistently, and it does not catch
"Top 3 Win", "Winner Take All", "50-50!" or "100-Player".

## The two numbers that do separate them

    cash rate         share of the field paid
    min cash multiple smallest prize divided by the entry fee

Measured on real DraftKings contests by this project:

    standard GPP    20-25% of the field pays, minimum cash about 2x
    Super Booster   2-3% pays, minimum cash 15-25x
    double-up       ~50% pays, minimum cash ~1.8x

Those do not overlap, so the shape is decidable when the curve is known -- and
refusing to guess when it is not is the whole point of `UNKNOWN`.
"""
from __future__ import annotations

GPP = "gpp"
FLAT_GPP = "flat_gpp"
DOUBLE_UP = "double_up"
LOTTERY = "lottery"
UNKNOWN = "unknown"

# Smallest top prize, as a multiple of the buy-in, worth entering a stacked
# lineup into. Below this a tournament build is taking tournament variance for
# a payoff that cannot repay it.
MIN_TOP_MULTIPLE = 50


def classify(cash_rate: float | None, min_cash_multiple: float | None,
             top_multiple: float | None = None) -> str:
    """
    What kind of contest is this?

    Returns UNKNOWN when either input is missing. That is deliberate: the whole
    reason this module exists is that a contest whose shape is unknown was being
    assumed to be a tournament, and the assumption was wrong at least once on
    every board looked at so far.
    """
    if cash_rate is None or min_cash_multiple is None:
        return UNKNOWN
    if cash_rate >= 0.35 and min_cash_multiple <= 2.5:
        return DOUBLE_UP
    if cash_rate <= 0.10 or min_cash_multiple >= 10:
        return LOTTERY
    if 0.10 < cash_rate <= 0.35:
        # Cash rate and minimum cash say "tournament" for all of these. They do
        # not say what there is to WIN, and that turned out to be the dimension
        # that matters most here. On one Week 2 board:
        #
        #   NFL $175K Fair Catch    24.1% paid, 1.5x min, top prize  1,250x
        #   NFL $50K 1st and 10     23.0% paid, 2.0x min, top prize    500x
        #   NFL $100K Blind Side    22.9% paid, 2.0x min, top prize    370x
        #   NFL $20 100-Player      20.0% paid, 1.8x min, top prize     14x
        #
        # The last one is a tournament by every other measure and is not one in
        # any sense this project cares about. Stacking exists to buy the upper
        # tail; in a contest topping out at 14x the buy-in there is no tail to
        # buy, and the variance is being taken for nothing.
        if top_multiple is not None and top_multiple < MIN_TOP_MULTIPLE:
            return FLAT_GPP
        return GPP
    return UNKNOWN


def from_tiers(entry_fee: float, max_entries: int, tiers: list[dict]) -> dict:
    """
    Summarise a payout curve.

    `tiers` are {"from": rank, "to": rank, "prize": amount}. Returns the cash
    rate, the minimum-cash multiple, the implied rake and the classification.
    """
    if not tiers or not max_entries or not entry_fee:
        return {"cashRate": None, "minCashMultiple": None, "rake": None, "shape": UNKNOWN}
    paid = sum(int(t["to"]) - int(t["from"]) + 1 for t in tiers)
    pool = sum((int(t["to"]) - int(t["from"]) + 1) * float(t["prize"]) for t in tiers)
    collected = entry_fee * max_entries
    cash_rate = paid / max_entries
    min_multiple = min(float(t["prize"]) for t in tiers) / entry_fee
    top_multiple = max(float(t["prize"]) for t in tiers) / entry_fee
    return {
        "cashRate": round(cash_rate, 4),
        "minCashMultiple": round(min_multiple, 2),
        "topPrizeMultiple": round(top_multiple, 1),
        "paidPlaces": paid,
        "prizePool": pool,
        "rake": round((collected - pool) / collected, 4) if collected else None,
        "shape": classify(cash_rate, min_multiple, top_multiple),
    }


def playable_shape(shape: str) -> bool:
    """
    Should a tournament build be entered here?

    Only a GPP with a top prize worth chasing.

    A double-up pays for clearing the median, which is the opposite of what a
    stacked, high-variance lineup is built to do. A lottery paying three of two
    hundred is not a tournament in any useful sense. A FLAT_GPP has the right
    payout shape and nothing at the top -- the $20 100-Player pays 20% of the
    field but tops out at 14x the buy-in, so the tail stacking buys is worth
    almost nothing there. UNKNOWN is refused, because the alternative is
    guessing, and guessing is what put a double-up at the head of the plan.
    """
    return shape == GPP

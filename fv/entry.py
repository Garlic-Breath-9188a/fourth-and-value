"""
Which contests to enter, given a weekly budget.

Two constraints beat everything else here, and neither is about lineups:

  Rake is not flat. Across the 109-contest Week 1 board, $3-$5 contests keep
  15.0% of every dollar entered and $100+ contests keep 9.7% (Spearman 0.86,
  n=100). Buy-in is the one free lever in the whole tool.

  A lineup entered into the wrong slate cannot be entered at all. Contests are
  filtered to the slate the lineups were built for, before anything else.
"""
from __future__ import annotations
from dataclasses import dataclass

MEANINGFUL_STEP = 1.5   # a bigger contest must be at least this multiple to be worth it


@dataclass
class Entry:
    contest: dict
    lineup_index: int

    @property
    def fee(self) -> float:
        return self.contest["entryFee"]


def playable(contests: list[dict], lock_label: str | None) -> list[dict]:
    """Only tournaments, only on our slate, only with prizes."""
    out = [c for c in contests
           if c.get("structure") == "gpp" and c.get("totalPrizes", 0) > 0]
    if lock_label:
        out = [c for c in out if c.get("lockTime") == lock_label]
    return out


def rake_pct(c: dict) -> float | None:
    """
    What the house keeps, against a FULL field.

    Guaranteed contests pay their advertised prize pool whether or not they
    fill, so measuring against the current fill of a contest that is 25% full
    reports a hugely negative rake -- true today, and not what will be true at
    lock. maxEntries is the number the advertised pool is priced against.
    """
    field = c.get("maxEntries") or 0
    if not field or not c.get("entryFee"):
        return None
    handle = field * c["entryFee"]
    return round(100 * (1 - c["totalPrizes"] / handle), 1) if handle > 0 else None


def plan(contests: list[dict], budget: float, lineups: int,
         lock_label: str | None = None) -> tuple[list[Entry], list[str]]:
    """
    Spread the budget across one entry per lineup, buying up where it fits.

    Funds a mid tier BEFORE upgrading a single entry to something larger: one
    $27 seat and nine $3 seats spends the budget but concentrates the week on
    one lineup, and the rake saved on that seat does not pay for the
    concentration.
    """
    pool = sorted(playable(contests, lock_label), key=lambda c: c["entryFee"])
    notes: list[str] = []
    if not pool:
        return [], ["No tournaments on this slate in the contest file."]

    fees = sorted({c["entryFee"] for c in pool})
    base = fees[0]
    if base * lineups > budget:
        affordable = int(budget // base)
        notes.append(
            f"${budget:,.0f} covers {affordable} of {lineups} lineups at the cheapest "
            f"tournament on the board (${base:,.0f}). Entering fewer.")
        lineups = max(1, affordable)

    chosen_fees = [base] * lineups
    spend = base * lineups
    # Buy up one seat at a time, cheapest step first, so the money spreads.
    improved = True
    while improved:
        improved = False
        for i in range(lineups):
            nxt = next((f for f in fees
                        if f > chosen_fees[i] and f >= chosen_fees[i] * MEANINGFUL_STEP), None)
            if nxt and spend - chosen_fees[i] + nxt <= budget:
                spend += nxt - chosen_fees[i]
                chosen_fees[i] = nxt
                improved = True
                break

    # Assign a contest to each fee, respecting each contest's own entry limit.
    # A [Single Entry] contest takes one lineup; entering it twice is not a
    # rounding error, it is a lineup that cannot be submitted.
    entries: list[Entry] = []
    filled: dict[str, int] = {}
    for i, fee in enumerate(sorted(chosen_fees, reverse=True)):
        options = [c for c in pool if c["entryFee"] == fee
                   and filled.get(c["id"], 0) < c.get("maxEntriesPerUser", 1)]
        if not options:
            notes.append(f"No room left in any ${fee:,.0f} tournament for lineup {i + 1}.")
            continue
        # Prefer the contest keeping least, then the largest prize pool.
        best = min(options, key=lambda c: (rake_pct(c) if rake_pct(c) is not None else 99,
                                           -c["totalPrizes"]))
        filled[best["id"]] = filled.get(best["id"], 0) + 1
        entries.append(Entry(best, i))

    left = budget - sum(e.fee for e in entries)
    if left >= base:
        notes.append(f"${left:,.0f} unspent — the next step up costs more than that.")
    return entries, notes

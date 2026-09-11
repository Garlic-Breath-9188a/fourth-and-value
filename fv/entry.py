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
    reason: str = ""

    @property
    def fee(self) -> float:
        return self.contest["entryFee"]


def _reason(chosen: dict, same_fee: list[dict]) -> str:
    """Why this contest and not another at the same price."""
    bits: list[str] = []
    rake = rake_pct(chosen)
    others = [c for c in same_fee if c["id"] != chosen["id"]]
    rivals = [r for r in (rake_pct(c) for c in others) if r is not None]
    if rake is not None:
        if rivals and rake < min(rivals):
            bits.append(f"keeps the least of the {len(same_fee)} tournaments at this price "
                        f"({rake}%, next best {min(rivals)}%)")
        else:
            bits.append(f"{rake}% rake")
    if chosen.get("maxEntriesPerUser") == 1:
        bits.append("single-entry, so nobody can play 150 lineups against your one")
    elif chosen.get("maxEntriesPerUser", 1) >= 100:
        bits.append(f"but opponents may enter up to {chosen['maxEntriesPerUser']} lineups")
    if chosen.get("totalPrizes", 0) >= 250_000:
        bits.append(f"${chosen['totalPrizes']:,} pool, so the top end is worth chasing")
    return "; ".join(bits) if bits else "only tournament at this price on the slate"


def playable(contests: list[dict], lock_label: str | None,
             window: str | None = "main") -> list[dict]:
    """
    Only tournaments, only ones this lineup can actually be entered in.

    Lock time alone is NOT enough to decide that. DraftKings runs "Early Only"
    contests that lock at the same 1:00pm as the main slate but contain only the
    early games -- 18 of them on the current board. A main-slate lineup holding
    a 4:25pm player cannot be entered in one, and matching on lock time would
    have offered them anyway.

    Boards captured before the window field existed have no such marking, so
    the filter is skipped rather than silently dropping every contest.
    """
    out = [c for c in contests
           if c.get("structure") == "gpp" and c.get("totalPrizes", 0) > 0]
    if lock_label:
        out = [c for c in out if c.get("lockTime") == lock_label]
    if window and any("window" in c for c in contests):
        out = [c for c in out if c.get("window") == window]
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
         lock_label: str | None = None,
         window: str | None = "main") -> tuple[list[Entry], list[str]]:
    """
    Spread the budget across one entry per lineup, buying up where it fits.

    Funds a mid tier BEFORE upgrading a single entry to something larger: one
    $27 seat and nine $3 seats spends the budget but concentrates the week on
    one lineup, and the rake saved on that seat does not pay for the
    concentration.
    """
    pool = sorted(playable(contests, lock_label, window), key=lambda c: c["entryFee"])
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
        entries.append(Entry(best, i, _reason(best, [c for c in pool if c["entryFee"] == fee])))

    left = budget - sum(e.fee for e in entries)
    if left >= base:
        notes.append(f"${left:,.0f} unspent — the next step up costs more than that.")
    return entries, notes

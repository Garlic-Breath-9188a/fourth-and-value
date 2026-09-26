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


def booster_shaped(c: dict) -> bool:
    """
    Is this a Booster, rather than a tournament?

    DraftKings marks Double Ups with structure "double_up" and they are filtered
    out on that. **Super Boosters are marked "gpp"** and are not, so they reached
    the plan and were recommended -- a $50 "NFL $10K Super Booster [Top 10 Win
    $1,000]" was offered as half a $100 budget. That is the bug this catches.

    They are a different product wearing a tournament's label. A standard
    DraftKings GPP pays 20-25% of the field and minimum cash is roughly twice
    the buy-in. A Booster pays a **2-3% cash rate with minimum cash at 15-25x**:
    a handful of identical large prizes and nothing else, which the name states
    outright ("Top 10 Win $1,000").

    That was measured, not assumed -- see the C2/C3 rows in the strategy ledger,
    where it is recorded as "confirmed and then some". Rake does not see it: a
    Booster can show a perfectly ordinary rake while paying almost nobody, so
    ranking on rake alone will keep choosing them.

    Detection is on the name because the lobby file carries no payout curve, and
    because DraftKings names the product consistently. If a future board marks
    them with their own `structure`, prefer that.
    """
    name = c.get("name", "").lower()
    return "booster" in name or "double up" in name


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
    # structure must be a KNOWN gpp. "unknown" is refused rather than assumed:
    # the $20 50-50! and the $20 200-Player (Top 3 Win) sit on the same board at
    # the same $20 fee, the same $1,800-and-$3,600 pools and the SAME 10.0%
    # rake, and one pays half the field at 1.8x while the other pays three of
    # two hundred at 36x. Nothing the lobby displays separates them.
    out = [c for c in contests
           if c.get("structure") == "gpp" and c.get("totalPrizes", 0) > 0
           and not booster_shaped(c)]
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
    Assign each lineup a contest, best contests first.

    Delegates the CHOICE to `select.build`, which scores rake, top-heaviness,
    single-entry and overlay, and spreads across distinct contests before
    re-entering one. This function only maps the result onto lineup slots.

    It used to ladder fees instead: start every lineup at the cheapest tournament
    on the board, then buy seats up one at a time with whatever was left. On a
    $100 budget with ten lineups at a $10 minimum there is nothing left, so it
    put ALL TEN into the single cheapest contest -- which on the Week 3 board was
    the WORST rake available at 15.0%, while three 12.0% contests went unused.
    It also disagreed with the "Best contests for your budget" panel on the same
    screen, which was reading from `select.build` and saying something else
    entirely. Two selectors, two answers, and the tab showed the worse one.

    Fewer, better seats is the intended behaviour: if the budget funds four good
    contests and not ten poor ones, four lineups get assigned and the rest are
    reported unassigned rather than crammed into whatever is cheapest.
    """
    from . import select as _select

    picks, notes = _select.build(contests, budget, lineups, window or "main")
    if lock_label:
        picks = [p for p in picks if p["contest"].get("lockTime") in (None, lock_label)]
    entries = [Entry(p["contest"], i, p.get("why", "")) for i, p in enumerate(picks)]

    # Concentration must explain itself. select.build spreads across distinct
    # contests first and only then re-enters one, but the second pass is
    # invisible unless it says so.
    per: dict[str, int] = {}
    for e in entries:
        per[e.contest["name"]] = per.get(e.contest["name"], 0) + 1
    for name, n in sorted(per.items(), key=lambda kv: -kv[1]):
        if n > 1:
            notes.append(
                f"{n} of your lineups are in the same tournament ({name}). Every other contest "
                f"that fits the budget was already taken once first.")

    if entries and len(entries) < lineups:
        notes.append(
            f"{len(entries)} of {lineups} lineups have a contest. The rest are unassigned "
            f"because the budget buys better seats than it buys more of them -- raise the "
            f"budget or build fewer lineups.")
    left = budget - sum(e.fee for e in entries)
    if entries and left >= min(e.fee for e in entries):
        notes.append(f"${left:,.0f} unspent.")
    return entries, notes

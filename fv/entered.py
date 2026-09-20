"""
The record of what has actually been entered.

Kept in the browser session, so it survives moving between tabs and rebuilding
lineups, but not a browser refresh -- Streamlit Community Cloud gives an app no
per-user storage, and writing to disk would share one person's entries with
everyone who opens the page. The export button is the durable copy.

A saved lineup is identified by its roster, not by its position on the board:
lineup 3 becomes a different nine players the moment the seed changes, so
anything keyed on the slot number would silently point at the wrong roster.
"""
from __future__ import annotations
import csv, io

from .roster import order_roster


def roster_key(lineup: list[dict]) -> str:
    """Stable identity for a set of nine players, order-independent."""
    return "-".join(sorted(str(p["id"]) for p in lineup))


def record(entered: dict, lineup: list[dict], contest: dict, fee: float) -> dict:
    key = roster_key(lineup)
    entered[key] = {
        # `projection` is kept because order_roster needs it to decide which
        # player takes the FLEX. Trimming it out made the export crash.
        "players": [{"id": p["id"], "name": p["name"], "position": p["position"],
                     "team": p["team"], "salary": p["salary"],
                     "projection": p.get("projection", 0.0)} for p in lineup],
        "contest": contest.get("name", "—"),
        "contest_id": contest.get("id", ""),
        "fee": float(fee),
    }
    return entered


def forget(entered: dict, lineup: list[dict]) -> dict:
    entered.pop(roster_key(lineup), None)
    return entered


def is_entered(entered: dict, lineup: list[dict]) -> bool:
    return roster_key(lineup) in entered


def total_fees(entered: dict) -> float:
    return sum(e["fee"] for e in entered.values())


def used_player_ids(entered: dict) -> set[str]:
    return {p["id"] for e in entered.values() for p in e["players"]}


def to_csv(entered: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Contest", "Fee", "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"])
    for e in entered.values():
        ordered = order_roster(e["players"])
        w.writerow([e["contest"], f"{e['fee']:.2f}"] + [f'{p["name"]} ({p["id"]})'
                                                        for _, p in ordered])
    return out.getvalue()


# ---------------------------------------------------------------------------
# Entries actually placed on DraftKings.
#
# Everything above is the in-session tick-list: lineups this app generated that
# you have marked as entered. This is the other thing -- the real entries, read
# back from a transcription of the DraftKings entry screen, so the Entry plan
# tab can show what was ACTUALLY staked next to what it recommends.
#
# The file is optional and gitignored. This repo is public; a personal betting
# record is not committed to it. When the file is absent every function here
# returns empty and the tab hides the section rather than showing a broken one.
# ---------------------------------------------------------------------------

import json
from pathlib import Path


def empty_placed() -> dict:
    """
    A fresh empty record.

    A module-level constant copied with dict() was the obvious thing and it was
    wrong: that is a SHALLOW copy, so every caller shared one `entries` list and
    anything appended to it leaked into the next call. A function is the fix.
    """
    # "staked" not "budget": this is what has already been put down, which is a
    # different number from the weekly budget the app plans against. Conflating
    # the two made the Entry plan tab read as though $58 were the whole budget.
    return {"entries": [], "contests": [], "staked": 0.0, "note": "", "transcribed": ""}


def parse_placed(text: str) -> dict:
    """Validate a placed-entries document. Returns an empty record if it is not one."""
    try:
        data = json.loads(text)
    except ValueError:
        return empty_placed()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return empty_placed()
    data.setdefault("contests", [])
    data.setdefault("staked", 0.0)
    data.setdefault("note", "")
    data.setdefault("transcribed", "")
    return data


def load_placed(path: Path) -> dict:
    """The placed-entries file, or an empty record when it is not present."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return empty_placed()
    data.setdefault("entries", [])
    data.setdefault("contests", [])
    data.setdefault("staked", 0.0)
    return data


def placed_contest(data: dict, contest_id: str) -> dict:
    for c in data.get("contests", []):
        if c.get("id") == contest_id:
            return c
    return {}


def placed_fees(data: dict) -> float:
    return sum(placed_contest(data, e.get("contestId", "")).get("entryFee", 0.0)
               for e in data.get("entries", []))


def _name_team(players) -> frozenset:
    """
    Identity used to compare a placed roster against a generated one.

    NOT the player id. The placed entries are transcribed from a screen that
    shows an initial and a surname, so they carry no DraftKings id -- matching
    on name and team is the most that can honestly be done, and salary is
    checked separately by scripts/check-entries.mjs in the app repo.
    """
    out = set()
    for p in players:
        name = p.get("name", "")
        # "Bijan Robinson" from the pool vs "B. Robinson" from the screen.
        parts = name.replace(".", "").split()
        surname = parts[-1].lower() if parts else ""
        initial = parts[0][0].lower() if parts else ""
        out.add((initial, surname, p.get("team", "")))
    return frozenset(out)


def match_generated(placed_roster: list[dict], lineups: list[list[dict]]) -> int | None:
    """Index of the generated lineup with the same nine players, or None."""
    want = _name_team(placed_roster)
    for i, l in enumerate(lineups):
        if _name_team(l) == want:
            return i
    return None


def overlap_with(placed_roster: list[dict], lineup: list[dict]) -> int:
    """How many of the nine a placed entry shares with a generated lineup."""
    return len(_name_team(placed_roster) & _name_team(lineup))


def contests_with_room(contests: list[dict], entered: dict) -> list[dict]:
    """
    Contests that can still take one more of YOUR lineups.

    Two limits, and both bite. `maxEntriesPerUser` is how many of your own
    lineups DraftKings will accept -- offering a [Single Entry] contest twice is
    not a rounding error, it is a lineup that will be rejected at submission.
    `maxEntries` is the contest filling up, which happens for real: the $50K
    Blind Side was 2,109 of 2,159 about eighty minutes before lock.

    Ordered cheapest first, then by prize pool, because that is the order a
    person scanning a dropdown wants.
    """
    used: dict[str, int] = {}
    for rec in entered.values():
        cid = rec.get("contest_id", "")
        used[cid] = used.get(cid, 0) + 1
    room = [c for c in contests
            if used.get(c["id"], 0) < c.get("maxEntriesPerUser", 1)
            and c.get("entered", 0) < c.get("maxEntries", 10 ** 9)]
    room.sort(key=lambda c: (c["entryFee"], -c.get("totalPrizes", 0)))
    return room


def entries_left(contest: dict, entered: dict) -> int:
    """How many more of your lineups this contest will accept."""
    used = sum(1 for rec in entered.values()
               if rec.get("contest_id", "") == contest["id"])
    return max(0, contest.get("maxEntriesPerUser", 1) - used)


def _qb_keys(roster) -> set:
    """
    Identity of the quarterbacks in one roster, as (initial, surname, team).

    Placed entries are transcribed from a screen showing "T. Lawrence" while the
    pool holds "Trevor Lawrence", so this reuses the same loose identity
    `_name_team` uses. The team is part of the key deliberately: "C. Williams"
    is Caleb Williams at CHI and Javonte Williams at DAL, and both were on the
    same entered lineup.

    Placed rosters carry `slot`; session records carry `position`. A FLEX is
    never a quarterback, so reading either field is safe.
    """
    out = set()
    for p in roster:
        if (p.get("position") or p.get("slot")) != "QB":
            continue
        parts = (p.get("name") or "").replace(".", "").split()
        if not parts:
            continue
        out.add((parts[0][0].lower(), parts[-1].lower(), p.get("team", "")))
    return out


def quarterbacks_used(placed: dict | None = None, session: dict | None = None) -> set:
    """
    Every quarterback already staked, from both records of what was entered.

    Both are needed and they are different things: `placed` is the file
    transcribed from DraftKings, `session` is what was ticked in this browser.
    Reading only one of them would let a quarterback through.
    """
    keys: set = set()
    for e in (placed or {}).get("entries", []):
        keys |= _qb_keys(e.get("roster", []))
    for rec in (session or {}).values():
        keys |= _qb_keys(rec.get("players", []))
    return keys


def without_used_quarterbacks(pool: list[dict], used: set) -> list[dict]:
    """
    The pool minus quarterbacks already entered. Every other position is kept.

    One quarterback per week per portfolio is a concentration choice, not a
    measured edge -- and the measurement points the other way, since tighter
    quarterback caps cost P(180+) (1.12% to 0.83% at a 20% cap over 101 weeks).
    What it does buy is protection against the downside that WAS measured: when
    the quarterback a portfolio leans on busts, the chance every lineup fails
    rises from 32% to 53%.
    """
    if not used:
        return list(pool)
    out = []
    for p in pool:
        if p.get("position") == "QB":
            parts = (p.get("name") or "").replace(".", "").split()
            if parts and (parts[0][0].lower(), parts[-1].lower(), p.get("team", "")) in used:
                continue
        out.append(p)
    return out

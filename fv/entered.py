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

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

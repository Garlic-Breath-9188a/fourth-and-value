"""
Slot ordering and stack labelling — how a lineup is READ, not how it is built.

The board and the DraftKings CSV export use one ordering function, so what is on
screen is what gets uploaded.
"""
from __future__ import annotations
from .rules import POSITION_MIN

DK_SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")


def order_roster(players: list[dict]) -> list[tuple[str, dict]]:
    """
    Put a roster in DraftKings' own slot order.

    A legal lineup can hold three running backs or two tight ends -- the extra
    one is in the FLEX. Before this existed the board sorted by position and
    those lineups read as illegal rosters when they were fine. The lowest
    projected of an over-filled position takes the FLEX, since that is the one
    whose slot is least load-bearing.
    """
    by_pos: dict[str, list[dict]] = {}
    for p in players:
        by_pos.setdefault(p["position"], []).append(p)
    for group in by_pos.values():
        group.sort(key=lambda p: p["projection"], reverse=True)

    flex: dict | None = None
    for pos in ("RB", "WR", "TE"):
        group = by_pos.get(pos, [])
        while len(group) > POSITION_MIN[pos]:
            candidate = group.pop()
            if flex is None or candidate["projection"] < flex["projection"]:
                if flex is not None:
                    by_pos[flex["position"]].append(flex)
                flex = candidate

    out: list[tuple[str, dict]] = []
    for slot in DK_SLOTS:
        if slot == "FLEX":
            if flex:
                out.append(("FLEX", flex))
            continue
        group = by_pos.get(slot, [])
        if group:
            out.append((slot, group.pop(0)))
    return out


def stack_role(player: dict, lineup: list[dict]) -> str | None:
    """QB / STACK (his catcher) / BRING-BACK (a catcher from the other side)."""
    qb = next((p for p in lineup if p["position"] == "QB"), None)
    if not qb:
        return None
    if player["id"] == qb["id"]:
        return "QB"
    if player["position"] not in ("WR", "TE"):
        return None
    if player["team"] == qb["team"]:
        return "STACK"
    if player["team"] == qb["opponent"]:
        return "BRING-BACK"
    return None


def is_stacked(lineup: list[dict], mates: int = 2, bring_back: int = 1) -> bool:
    roles = [stack_role(p, lineup) for p in lineup]
    return roles.count("STACK") >= mates and roles.count("BRING-BACK") >= bring_back


def to_dk_csv(lineups: list[list[dict]]) -> str:
    header = "QB,RB,RB,WR,WR,WR,TE,FLEX,DST"
    lines = [header]
    for l in lineups:
        lines.append(",".join(f'{p["name"]} ({p["id"]})' for _, p in order_roster(l)))
    return "\n".join(lines) + "\n"

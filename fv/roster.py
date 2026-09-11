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
    """Plain export, for reading or for pasting into a spreadsheet."""
    lines = ["QB,RB,RB,WR,WR,WR,TE,FLEX,DST"]
    for l in lineups:
        lines.append(",".join(f'{p["name"]} ({p["id"]})' for _, p in order_roster(l)))
    return "\n".join(lines) + "\n"


def fill_dk_template(template_csv: str, lineups: list[list[dict]]) -> tuple[str, list[str]]:
    """
    Fill DraftKings' own bulk-upload template with these lineups.

    DraftKings will not accept an arbitrary CSV. Their importer works from a
    template YOU download after entering contests: it already carries an Entry
    ID, Contest Name, Contest ID and Entry Fee per entry, and those columns are
    how it knows which entry each row updates. So the flow is enter first,
    download the template, fill it, upload it back.

    Every original column is preserved byte-for-byte; only the nine position
    columns are written. Rows beyond the number of lineups are left untouched
    rather than blanked, since a blanked row would wipe an entry that may
    already hold a lineup.
    """
    import csv, io

    reader = csv.reader(io.StringIO(template_csv.lstrip("\ufeff")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    notes: list[str] = []
    if not rows:
        return "", ["That file is empty."]

    header = rows[0]
    upper = [c.strip().upper() for c in header]
    wanted = list(DK_SLOTS)
    # Find the run of position columns. They appear in DK slot order, and the
    # header repeats names (RB twice, WR three times), so they are located by
    # walking the header rather than by dict lookup.
    idx: list[int] = []
    cursor = 0
    for slot in wanted:
        try:
            found = upper.index(slot, cursor)
        except ValueError:
            return "", [f"This does not look like a DraftKings upload template — "
                        f"no '{slot}' column found after position {cursor}. Download the "
                        f"template from the contest's Enter/Edit screen."]
        idx.append(found)
        cursor = found + 1

    filled = 0
    for i, lineup in enumerate(lineups):
        row_no = i + 1
        if row_no >= len(rows):
            notes.append(f"The template has {len(rows) - 1} entries but you have "
                         f"{len(lineups)} lineups — {len(lineups) - (len(rows) - 1)} "
                         f"could not be placed. Enter more contests, or build fewer.")
            break
        row = rows[row_no]
        while len(row) <= idx[-1]:
            row.append("")
        for slot_col, (_, player) in zip(idx, order_roster(lineup)):
            row[slot_col] = str(player["id"])
        filled += 1

    if len(rows) - 1 > len(lineups):
        notes.append(f"{len(rows) - 1 - len(lineups)} template rows were left as they were, "
                     f"because there were no more lineups for them.")

    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    notes.insert(0, f"Filled {filled} of {len(rows) - 1} entries.")
    return out.getvalue(), notes

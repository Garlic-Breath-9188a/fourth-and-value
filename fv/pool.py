"""
The player pool, from a DraftKings salary export.

The export carries AvgPointsPerGame and no projection, so that average IS the
projection here -- a backward-looking number with no matchup, role or injury
information in it. That limitation is stated on screen rather than hidden.
"""
from __future__ import annotations
import csv, io, json, math
from pathlib import Path
from .rules import CEILING_MULTIPLE, UNROSTERABLE

STATUS = {"O": "Out", "Q": "Questionable", "D": "Doubtful", "IR": "IR"}


def _key(name: str) -> str:
    return "".join(c for c in str(name).lower() if c.isalpha())


def load_salaries(text: str) -> list[dict]:
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for r in reader:
        pos = (r.get("Position") or "").strip().upper()
        if pos not in CEILING_MULTIPLE:
            continue
        try:
            salary = int(float(r.get("Salary") or 0))
        except ValueError:
            continue
        if salary <= 0:
            continue
        info = (r.get("Game Info") or "").strip()
        game = info.split(" ")[0].upper() if info else ""
        team = (r.get("TeamAbbrev") or "").strip().upper()
        away, _, home = game.partition("@")
        try:
            avg = float(r.get("AvgPointsPerGame") or 0)
        except ValueError:
            avg = 0.0
        rows.append({
            "id": (r.get("ID") or f"{team}-{r.get('Name')}").strip(),
            "name": (r.get("Name") or "").strip(),
            "position": pos, "salary": salary, "team": team,
            "opponent": away if team == home else home,
            "game": game, "game_info": info,
            "projection": avg,
            "status": STATUS.get((r.get("Status") or "").strip().upper(), "Active"),
        })
    return rows


def keep_starting_quarterbacks(rows: list[dict]) -> list[dict]:
    """
    Drop backup quarterbacks.

    AvgPointsPerGame cannot tell a starter from a backup, and a backup priced at
    $4,000 on last season's 16-point average is the best points-per-dollar
    quarterback on the board. The optimizer is defenceless against that; the
    highest-salaried quarterback per team is taken as the starter.

    A heuristic about the market, not a depth chart. DraftKings does not
    reprice late scratches.
    """
    best: dict[str, dict] = {}
    for r in rows:
        if r["position"] != "QB":
            continue
        held = best.get(r["team"])
        if not held or (r["salary"], r["projection"]) > (held["salary"], held["projection"]):
            best[r["team"]] = r
    starters = {r["id"] for r in best.values()}
    return [r for r in rows if r["position"] != "QB" or r["id"] in starters]


def apply_ceilings(rows: list[dict], variance: dict | None) -> list[dict]:
    """
    A ceiling from each player's own scoring spread, where one is known.

    A flat multiple ranks nobody differently within a position, so the ceiling
    weight cannot prefer a volatile player over a steady one. Players with no
    history keep the multiple and are LABELLED, never given an invented spread.
    """
    table = (variance or {}).get("players", {})
    for r in rows:
        rec = table.get(_key(r["name"]))
        flat = r["projection"] * CEILING_MULTIPLE.get(r["position"], 2.0)
        if rec and rec.get("games", 0) >= 5 and rec.get("mean", 0) > 0.5:
            ratio = rec["p80"] / rec["mean"]
            r["ceiling"] = r["projection"] * max(1.15, min(2.8, ratio))
            r["ceiling_source"] = f"own 80th percentile ({rec['games']} games)"
        else:
            r["ceiling"] = flat
            r["ceiling_source"] = "positional estimate — no scoring history"
    return rows


def rosterable(row: dict, exclude_questionable: bool = False) -> bool:
    if row["status"] in UNROSTERABLE:
        return False
    return not (exclude_questionable and row["status"] == "Questionable")


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None

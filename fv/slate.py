"""
Which games a contest actually contains.

A DraftKings salary export covers the whole week -- Week 1 2026 runs Wednesday
to Monday across 16 games -- while most Classic tournaments are the Sunday main
slate. Without this filter the optimizer produces lineups containing players
whose games have already kicked off, or who play on Monday night. Those lineups
look fine and cannot be entered.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from .rules import WINDOW_GAP_HOURS

_KICKOFF = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})(AM|PM)", re.I)


def parse_kickoff(game_info: str) -> datetime | None:
    """`"NO@DET 09/13/2026 01:00PM ET"` -> the kickoff, or None."""
    m = _KICKOFF.search(game_info or "")
    if not m:
        return None
    mo, da, yr, hh, mm, ap = m.groups()
    hour = int(hh) % 12 + (12 if ap.upper() == "PM" else 0)
    return datetime(int(yr), int(mo), int(da), hour, int(mm))


def kickoff_windows(times: list[datetime]) -> list[list[datetime]]:
    """Group kickoffs into windows, splitting on any gap over the threshold."""
    windows: list[list[datetime]] = []
    for t in sorted(set(times)):
        if windows and (t - windows[-1][-1]) <= timedelta(hours=WINDOW_GAP_HOURS):
            windows[-1].append(t)
        else:
            windows.append([t])
    return windows


@dataclass(frozen=True)
class Slate:
    locks_at: datetime
    ends_at: datetime
    label: str
    games: tuple[str, ...]

    @property
    def game_count(self) -> int:
        return len(self.games)


def in_slate(kickoff: str | None, locks_at: datetime, ends_at: datetime | None = None) -> bool:
    """
    A player belongs if his game starts at or after the lock and no later than
    the slate's last kickoff.

    A player whose kickoff cannot be parsed is KEPT. Dropping him would shrink
    the pool silently on a parse failure, and an extra player on the board is
    visible where a missing one is not.
    """
    k = parse_kickoff(kickoff or "")
    if k is None:
        return True
    if k < locks_at:
        return False
    return True if ends_at is None else k <= ends_at


def slate_options(players: list[dict]) -> list[Slate]:
    """Every slate the file supports: each run of consecutive kickoff windows."""
    times = [parse_kickoff(p.get("game_info", "")) for p in players]
    times = [t for t in times if t]
    if not times:
        return []
    windows = kickoff_windows(times)
    out: list[Slate] = []
    for i in range(len(windows)):
        for j in range(len(windows) - 1, i - 1, -1):
            start, end = windows[i][0], windows[j][-1]
            games = sorted({p["game"] for p in players
                            if in_slate(p.get("game_info"), start, end) and p.get("game")})
            if len(games) < 2:            # DraftKings requires two games
                continue
            label = start.strftime("%a %-m/%-d %-I:%M %p")
            if j > i:
                label += " – " + end.strftime("%a %-I:%M %p")
            out.append(Slate(start, end, label, tuple(games)))
    return sorted(out, key=lambda s: (s.locks_at, -s.game_count))


def main_slate(options: list[Slate]) -> Slate | None:
    """
    The slate a DraftKings "Main" contest means: the shortest run starting on a
    Sunday afternoon.

    Taking the option with the most games would return the full-week slate,
    which is what put Monday-night players in a Sunday lineup.
    """
    afternoon = [s for s in options
                 if s.locks_at.weekday() == 6 and 11 <= s.locks_at.hour < 17]
    if not afternoon:
        return options[0] if options else None
    return min(afternoon, key=lambda s: s.ends_at)


def restrict(players: list[dict], slate: Slate) -> tuple[list[dict], list[str]]:
    """Filter to one slate, returning the players kept and the games dropped."""
    kept = [p for p in players if in_slate(p.get("game_info"), slate.locks_at, slate.ends_at)]
    dropped = sorted({p["game"] for p in players
                      if not in_slate(p.get("game_info"), slate.locks_at, slate.ends_at)})
    return kept, dropped

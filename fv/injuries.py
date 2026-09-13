"""
A live injury check, to cross-examine the salary file.

The DraftKings export carries a Status column, but it is frozen at the moment
the file was pulled. A player ruled out on Friday still reads Active in a
Wednesday export, and a lineup built on it scores zero for that slot.

Source is Sleeper's public player API: free, no key, documented at
docs.sleeper.com, and explicitly open for this kind of use. It carries a body
part as well as a status, which matters because the mispricing measured in this
project is body-part specific -- hamstrings and calves miss their price, knees
and ankles do not.

The app degrades to the salary file when the feed cannot be reached. It never
silently substitutes one for the other: a disagreement is shown as a
disagreement, because which of the two is right is a judgment for the person
entering the contest.
"""
from __future__ import annotations
import json
import re
import urllib.request

ENDPOINT = "https://api.sleeper.app/v1/players/nfl"
TIMEOUT = 25

# Body parts whose first-game-back return is NOT priced in by DraftKings,
# measured over 1,124 returns from 2017-2025 injury reports. Effects are in
# standard deviations of the salary residual; one SD is about 6.9 DK points.
#
#   hamstring  -0.185 SD  [-0.324, -0.046]  186 games
#   foot       -0.256 SD  [-0.413, -0.099]   47 games
#   calf       -0.438 SD  [-0.659, -0.217]   31 games
#
# Knee, ankle, shoulder, concussion, groin, hip and back all span zero and are
# deliberately absent -- fading them would be acting on noise. Groin in
# particular is often assumed to be a soft-tissue risk; it measured -0.071 SD
# with an interval from -0.462 to +0.321, which is no evidence either way.
MISPRICED_PARTS = {"hamstring": -0.185, "foot": -0.256, "calf": -0.438}

BLOCKING = {"Out", "IR", "Doubtful", "PUP", "NFI", "Sus", "DNR"}


# Generational suffixes, which the two sources disagree about. DraftKings
# writes "Michael Penix Jr."; Sleeper writes "Michael Penix". Of 238 players on
# the wire exactly one carried a suffix, against 59 in the salary file.
_SUFFIX = re.compile(r"\b(?:jr|sr|ii|iii|iv|v)\b\.?", re.I)


def _norm(name: str) -> str:
    """
    A join key both sources agree on.

    The suffix has to go. Without stripping it, "Michael Penix Jr." keys to
    michaelpenixjr and the wire's "Michael Penix" keys to michaelpenix, so the
    two never meet -- and Penix stayed rosterable in the app on a stale
    Questionable while the wire had him Out after ACL surgery. Nine players were
    hidden this way on the Week 1 board, two of them rosterable-but-out.

    Checked for collisions before adopting: stripping suffixes merges no two
    distinct players on this slate.
    """
    return "".join(c for c in _SUFFIX.sub("", name or "").lower() if c.isalpha())


def fetch(url: str = ENDPOINT) -> dict | None:
    """Every NFL player Sleeper knows about, or None if the feed is unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def index(raw: dict | None) -> dict[str, dict]:
    """Name -> {status, body_part, note, team}, for players carrying a status."""
    out: dict[str, dict] = {}
    for p in (raw or {}).values():
        if p.get("position") not in ("QB", "RB", "WR", "TE"):
            continue
        status = p.get("injury_status")
        if not status:
            continue
        name = p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        if not name:
            continue
        out[_norm(name)] = {
            "status": status,
            "body_part": (p.get("injury_body_part") or "").strip(),
            "note": (p.get("injury_notes") or "").strip(),
            "team": p.get("team") or "",
        }
    return out


def mispriced_fade(body_part: str) -> tuple[str, float] | None:
    """The measured fade for this body part, if there is one."""
    part = (body_part or "").lower()
    for key, sd in MISPRICED_PARTS.items():
        if key in part:
            return key, sd
    return None


def cross_check(players: list[dict], feed: dict[str, dict]) -> list[dict]:
    """
    Where the live feed and the salary file disagree.

    Only disagreements that would change a decision are reported: the feed
    saying a player cannot play when the salary file has him available. The
    reverse -- the file being more pessimistic than the feed -- is left alone,
    since nobody is harmed by leaving a player out.
    """
    found: list[dict] = []
    for p in players:
        live = feed.get(_norm(p["name"]))
        if not live:
            continue
        file_blocks = p.get("status") in BLOCKING
        live_blocks = live["status"] in BLOCKING
        if live_blocks and not file_blocks:
            found.append({**p, "live_status": live["status"],
                          "body_part": live["body_part"], "note": live["note"],
                          "issue": "listed as playable in the salary file"})
        elif live["status"] == "Questionable" and p.get("status") == "Active":
            fade = mispriced_fade(live["body_part"])
            found.append({**p, "live_status": "Questionable",
                          "body_part": live["body_part"], "note": live["note"],
                          "issue": (f"questionable — {fade[0]} returns miss their price by "
                                    f"{fade[1]:+.3f} SD" if fade else "questionable")})
    return found

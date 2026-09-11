"""
Per-player context: what is pushing a player's outlook up or down, and --
just as important -- what this build cannot see.

Every factor here is either read from the slate file or from a fixed table. No
factor is inferred, and nothing that needs data this build does not carry is
guessed at. The unavailable ones are named on screen rather than left out,
because a context column that silently omits weather reads as "weather is fine".
"""
from __future__ import annotations

# Roof by home team. Retractable is its own category and is NOT counted as a
# dome: whether it is open is a game-day decision by the home club, so treating
# it as indoors would be an assumption dressed as a fact.
ROOF = {
    "ARI": "retractable", "ATL": "retractable", "BAL": "outdoor", "BUF": "outdoor",
    "CAR": "outdoor", "CHI": "outdoor", "CIN": "outdoor", "CLE": "outdoor",
    "DAL": "retractable", "DEN": "outdoor", "DET": "dome", "GB": "outdoor",
    "HOU": "retractable", "IND": "retractable", "JAX": "outdoor", "KC": "outdoor",
    "LAC": "dome", "LAR": "dome", "LV": "dome", "MIA": "outdoor", "MIN": "dome",
    "NE": "outdoor", "NO": "dome", "NYG": "outdoor", "NYJ": "outdoor",
    "PHI": "outdoor", "PIT": "outdoor", "SEA": "outdoor", "SF": "outdoor",
    "TB": "outdoor", "TEN": "outdoor", "WAS": "outdoor",
}

# Measured player-level effects, in standard deviations of the salary residual,
# from 158 slates. One SD is about 6.9 DK points. These are shown as context,
# NOT applied to the projection -- the lineup-level test of home and dome
# together returned +1.55 points against the ~3.6 they would give if the
# player-level effects simply added up across a roster. They do not add up.
HOME_SD = 0.059
DOME_SD = 0.063


def home_team(game: str) -> str:
    """`"NO@DET"` -> `"DET"`."""
    return game.partition("@")[2].upper() if "@" in game else ""


def factors(player: dict) -> list[tuple[str, float | None]]:
    """
    (label, effect in SD) for each factor we can actually establish.

    None as the effect means "real but unquantified here" — a status flag
    rather than a measured trait.
    """
    out: list[tuple[str, float | None]] = []
    host = home_team(player.get("game", ""))
    if host:
        if player["team"] == host:
            out.append(("home", HOME_SD))
        else:
            out.append(("away", -HOME_SD))
        roof = ROOF.get(host)
        if roof == "dome":
            out.append(("dome", DOME_SD))
        elif roof == "retractable":
            out.append(("retractable roof", None))
    if player.get("status") == "Questionable":
        out.append(("questionable", None))
    if "no scoring history" in player.get("ceiling_source", ""):
        out.append(("no scoring history", None))
    return out


def summary(player: dict) -> str:
    """One cell for the pool table."""
    bits = []
    for label, sd in factors(player):
        if sd is None:
            bits.append(label)
        else:
            bits.append(f"{label} {sd:+.3f}")
    return ", ".join(bits) if bits else "—"


def net_sd(player: dict) -> float:
    """Sum of the quantified factors only. Context, not a projection change."""
    return round(sum(sd for _, sd in factors(player) if sd is not None), 3)


# Named so the app can say what it is blind to rather than implying all-clear.
NOT_AVAILABLE = [
    ("Wind", "Quarterbacks in 15+ mph wind score 0.292 SD below their price. "
             "This build ships a fixed slate snapshot and fetches no forecast."),
    ("First game back from injury", "Worth about 0.107 SD, because the player is on a "
                                    "snap count. Needs a weekly injury-report feed, which "
                                    "is not in this build."),
    ("Opponent strength by position", "A measured ±4% projection adjustment. Needs the "
                                      "defence-vs-position file, not carried here."),
    ("Vegas totals and spreads", "The largest measured effects — a defence favoured by 7+ "
                                 "is worth 0.458 SD — all need betting lines. None are here."),
    ("Revenge games", "Tested and flat. Nothing to show."),
]

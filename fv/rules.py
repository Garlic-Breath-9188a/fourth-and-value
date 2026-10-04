"""
The measured rules, ported from the TypeScript app.

Every constant here was set by a held-out measurement, not by preference, and
each one carries the result that set it. They are gathered in one file so a
reader can see the whole set of decisions at once, and so changing one is
obviously a change to a measured default rather than a tweak.

Source of record is the TypeScript app in ../app; this is a port for sharing.
Where the two disagree, the TypeScript is authoritative -- it has the harnesses
that produced these numbers.
"""

SALARY_CAP = 50_000
ROSTER_SIZE = 9
MIN_DISTINCT_GAMES = 2
MAX_PLAYERS_PER_TEAM = 8          # a real DraftKings rule, verified against their published rules

# Roster minimums. The ninth slot is the FLEX.
POSITION_MIN = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}

# ---------------------------------------------------------------------------
# Measured defaults
# ---------------------------------------------------------------------------

FLEX_POSITIONS = ("RB", "WR")
"""
No tight end in the FLEX.

100 weeks, ~120,000 stacked lineups, on P(a 180-point week):
    FLEX = RB only    0.36%   +0.112pp [ 0.034,  0.189]
    FLEX = RB or WR   0.30%   +0.054pp [ 0.026,  0.082]
    any FLEX          0.25%        --
    FLEX = TE only    0.17%   -0.082pp [-0.126, -0.039]

A tight end there cuts the chance of a winning week by about a third, and is
worse on the mean too (93.1 vs 96.4). Not an upside-for-floor trade; worse at
both ends.
"""

STACK_MATES = 2
STACK_BRING_BACK = 1
"""
Quarterback plus two of his pass catchers, plus one from the opposing team.

101 weeks, 202,000 lineups. Does NOT raise the mean (-0.31, not significant)
and nearly doubles P(180+) from 0.13% to 0.25%. The bring-back is the piece
that carries it: a second same-team receiver alone only reached 0.17%.
"""

QB_EXPOSURE_PCT = 10
RB_EXPOSURE_PCT = 10
OTHER_EXPOSURE_PCT = 10
EXPOSURE_PCT = 75
"""
How much of the portfolio one player may occupy.

Three numbers, not one, because they do different jobs:

  QB_EXPOSURE_PCT   the quarterback slider's default
  RB_EXPOSURE_PCT   the running back slider's default
  OTHER_EXPOSURE_PCT  the default for WR, TE and DST -- every position that
                      is not a quarterback or a running back
  EXPOSURE_PCT        the hard fallback if a position is somehow not covered

WR had no cap of its own and fell through to EXPOSURE_PCT at 75%, which at ten
lineups is SEVEN. Denzel Boston appeared in 7 of 10 generated lineups and then
in three of the four entries actually placed, which is how a week ends up riding
on one receiver. A lineup carries three WRs and usually a fourth at FLEX, so
receivers are the position where an uncapped default does the most damage --
exactly the opposite of where the attention had gone.

**A cap only does something while it binds.** On the Week 2 2026 slate no running
back appeared in more than 6 of 10 lineups unprompted, so at a count of 10 every
setting from 60% upward produced an identical portfolio. The RB slider used to
default to EXPOSURE_PCT (75%), which sits inside that dead zone -- moving it
looked broken because it genuinely changed nothing. 30% keeps the default in the
live part of the range.

Note that tighter caps were MEASURED and they cost the tail: P(180+) fell from
1.12% to 0.83% at a 20% quarterback cap over 101 weeks.

## The current defaults are 10%, and that is a preference with a known price

At ten lineups a 10% cap means **each player may appear exactly once**, so the
ten lineups share nobody. This is a deliberate choice by the user, made twice,
and it is not an improvement -- it is the most expensive setting on the board.
Measured on the Week 4 2026 slate against a clean pool of 450:

    caps 33 / 30 / 40     best ceiling 233.9   mean 211.6
    caps 20 / 20 / 20     best ceiling 228.8   mean 205.8
    caps 10 / 10 / 10     best ceiling 227.4   mean 172.6

The best lineup barely moves. The MEAN collapses by about 36 points, because
once the strongest players are spent on the first lineup the rest are built
from what is left, and the tenth is built from leftovers. You are trading nine
mediocre lineups for one good one and maximum coverage of the slate.

It also costs sampling. At 20,000 attempts a 10% cap returned only NINE
lineups -- the builder could not find a tenth that satisfied every constraint
without reusing someone. `ATTEMPTS_DEFAULT` is raised to compensate; if a board
ever comes back short, that is why.
"""

ATTEMPTS_DEFAULT = 80000
"""
How many random lineups the builder draws before picking the best ten.

20,000 was enough while players could repeat across lineups. At the 10% caps
above it is not: the search returned nine lineups rather than ten, silently.
80,000 reliably returns ten. The builder is a sampler, not an exact optimizer --
an exact dynamic-programming optimum beat 20,000 samples 169.7 to 161.5 on the
Week 2 slate -- so more attempts buy a little, with diminishing returns.
"""

CEILING_WEIGHT_DEFAULT = 1.0
"""
How far the builder leans on each player's ceiling rather than his mean.

0 chases the expected score, 1 chases the best case. This is a live control here,
unlike the Next.js build where the live pool's ceiling is a flat per-position
multiple and therefore cannot reorder anyone within a position: every player in
this pool carries a ceiling derived from his own scoring spread, with ratios
observed from 0.25 to 2.49, so the setting changes which players are picked at
every step of the slider.

Set to 1.0 as a user preference for a tournament build. It is NOT a measured
improvement -- the "Ceiling Only" arm of the ablation showed no detectable
difference over 139 weeks -- and it is not harmful either. A top-heavy payout only
pays for the tail, which is the argument for it; the measurement simply has not
resolved it.
"""

MAX_SHARED_PLAYERS = 7
"""Two lineups sharing 8 of 9 players are not two lineups."""

WIND_QB_MARKDOWN = 0.10
WINDY_MPH = 15
"""
Quarterbacks in 15+ mph wind score 0.292 SD below what their salary implies
(95% CI -0.477 to -0.107) -- about 2 DK points -- because their teams throw 3.6
fewer times. Pass catchers are NOT marked down: their effects all point the same
way and every one spans zero.
"""

FIRST_GAME_BACK_MARKDOWN = 0.07
SOFT_TISSUE_EXTRA = {"hamstring": 0.06, "foot": 0.08, "calf": 0.10}
"""
A player in his first game back scores 0.107 SD below his price, because he is
on a snap count -- 47.6% of snaps against a 57.0% norm. Gone by game two. Only
soft-tissue lower-body injuries are mispriced beyond that.
"""

UNROSTERABLE = {"Out", "IR", "Doubtful", "Suspended", "PUP", "NFI"}
"""
Not a judgment call. A player on injured reserve cannot play and scores zero;
26 of them were selectable on the Week 1 2026 slate before this existed.
"""

WINDOW_GAP_HOURS = 3.5
"""
A slate is a run of kickoffs with no larger gap. Week 1 2026 goes 1:00pm and
4:25pm (3h25m apart, one slate), then jumps 3h55m to Sunday night and again to
Monday. Without an upper bound the optimizer built lineups containing Monday
night players for a Sunday contest.
"""

CEILING_MULTIPLE = {"QB": 1.65, "RB": 1.95, "WR": 2.15, "TE": 2.20, "DST": 2.10}
"""
Fallback only, for players with no scoring history. A flat multiple ranks nobody
differently within a position, so it cannot tell a steady player from a volatile
one; where history exists, the player's own spread is used instead.
"""

NO_EDGE_WARNING = (
    "This tool recommends and exports. It never enters a contest, and it cannot tell you "
    "a lineup is safe. A real account measured here -- 571 entries over two seasons -- "
    "finished at the 49.1st percentile, which is what no edge looks like. Rake is 10-15% "
    "of every dollar entered. Everything measured in this project is worth a fraction of "
    "a point per player."
)

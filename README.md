# Fourth & Value

A DraftKings NFL Classic lineup builder for large-field tournaments, and an
honest record of how well it works.

**[Open the app](https://fourth-and-value.streamlit.app)** · built with Streamlit

<!-- The link above is the deployed instance. It used to point at
     https://share.streamlit.io, which is Streamlit's own homepage and not this app --
     so there was no way to reach the deployment from the repo, or to check that a push
     had actually gone live. Community Cloud also serves the auto-generated
     fourth-and-value-5i4r8soqyjhqydsgwycm9z.streamlit.app; both resolve here. -->

## What it does

Picks nine players under the $50,000 cap, builds five lineups (configurable)
that differ from each other, and suggests which tournaments to enter them in.
It never enters a contest for you.

Every construction rule in it was measured against past seasons before it
shipped, and each one is documented in `fv/rules.py` alongside the result that
set it. The short version:

- **Every lineup is stacked** — a quarterback, two of his own pass catchers, and
  one receiver from the opposing team. Over 101 weeks and 202,000 test lineups
  this did not raise the average score at all, but it nearly doubled the chance
  of a tournament-winning week, 0.13% to 0.25%. A stack doesn't make a lineup
  better; it makes it swingier, and a tournament only pays the top end.
- **No tight end in the FLEX** — measured over 100 weeks, an extra back or
  receiver there beats an extra tight end on both the average and the top end.
- **Upside comes from each player's own scoring spread**, not a flat multiple.
  Players without enough history are labelled rather than given a made-up number.
- **Only games in the contest you're entering.** Week 1 runs Wednesday to
  Monday; most tournaments are Sunday afternoon only.
- **Buy-in matters more than the model.** On a 109-contest board, $3–$5
  tournaments keep 15.0% of every dollar entered and $100+ keep 9.7%.
- **Projections blend this season with last.** DraftKings' average is a single
  game in Week 2 — one good Sunday reads as a 35-point forecast. The app weights
  it `n/(n+3)` against last season, which cuts projection error by more than
  forty times what the matchup adjustment is worth.
- **A live injury wire** (Sleeper's public API, refreshed every 6 hours) cross-
  checks the salary file, which is only ever as current as the moment it was
  exported — and fades specific body parts (hamstring, foot, calf) that are
  measured to miss their price on the first game back, where knee, ankle and
  concussion measure no effect and are left alone.
- **An "Own %" column** estimates what share of the field will draft each
  player, from a model fitted on real DraftKings DRAFT % and checked against
  real contest results (currently MAE 7.93 vs. a plain points-per-dollar
  guess's 8.81 — better, not perfect; see `fv/ownership.py` for exactly what
  it does and does not know). A leverage dial lets the builder actually trade
  on it, discounting heavily-owned players rather than only displaying them.
- **A "Matchup rank" column** shows each opponent's defence against the
  position, computed from this season's own completed games, refreshed
  automatically every week — not a bought snapshot that stops moving once
  purchased.
- **A role-opportunity board** flags a player whose same-team, same-position
  teammate is currently Out, Doubtful or on IR — a nameable reason a number
  might be durable, not a scored effect.

## What it does not do

About twenty ideas were tested here and **one** beat simply picking the
highest-projected legal lineup. Simulation-driven selection: no better.
Stars-and-scrubs: worse. Saving salary at the cheap roster slots: the two
cheapest players account for only 11% of a lineup's swing.

And the measurement that outranks the rest: an experienced player's real
account, 571 entries over two seasons, finished at the **49.1st percentile** —
dead average. His headline was +467% ROI, but 85% of it came from one
third-place finish out of 108,537 entries. Remove that week and he returns
−14%, almost exactly what the rake alone predicts.

There is no demonstrated edge here. Lower rake makes the hole shallower; it does
not make it a profit.

## What happened when it was actually played

Every slate of 2026 played so far is graded from the real contest results, not
a backtest. Season to date, through Week 6: **32 entries, $591 staked, $175
back — 29.6% ROI**, mean finishing percentile ~63.7%. An average lineup in a
12–15% rake tournament returns about 88%, not 29.6%; losing this much at this
volume is consistent with either "the model is bad" or "variance at this
sample size," and the sample is still too small to tell which. Week-by-week
detail, including a week that was first logged as zero entries because nobody
graded it until three weeks later, lives in `CLAUDE.md` and
`ENGINEERING_LOG.md` — not duplicated here because it changes every week and
this file does not.

The first two weeks, for the flavor of how it reads up close:

| | Week 1 | Week 2 | both |
|---|---|---|---|
| Entries | 10 | 6 | 16 |
| Staked | $105 | $98 | $203 |
| Returned | $90 | **$0** | $90 |
| ROI | −14.3% | **−100%** | **−55.7%** |
| Cashed | 2 of 10 | **0 of 6** | 2 of 16 |
| Mean finishing percentile | 60.4% | 63.4% | **61.5%** (95% CI 48.5–74.6) |

A player with no edge finishes at the 50th percentile and cashes about 23% of
the time, so both weeks read badly. The interval still spans 50, which is what
sixteen entries buys you — it is not evidence of an anti-edge. It is recorded
because it is the account rather than a backtest, and because this project
reports its bad numbers in the same place as its good ones.

**Week 2 came with an uncomfortable finding.** Every one of those six lineups was
built by hand on DraftKings' board rather than taken from this app, and a bug
meant the app's own board was not using the blended projection either. Scored on
actual points, the fixed board's best lineup beat the best hand-built one
156.4 to 130.1, and its *worst* lineup beat four of the six. One slate, so not a
measurement — but the direction is not flattering to either the bug or the
hand-building. One entry also lost Saquon Barkley to a first-series injury,
which no model could have carried.

One detail is worth more than the average: the two entries that cashed were the
two largest stakes, in two of the lowest-rake contests entered. The best contest
on the entire board finished 92nd percentile. That is the shape of the thing —
contest selection is a small real lever, and what any single lineup does is
noise.

## Run it locally

```sh
pip3 install -r requirements.txt
streamlit run app.py
```

## Tests

```sh
for t in test_*.py; do python3 -m unittest "$t"; done
```

188 tests across ten files, each named for the bug it prevents — Monday-night
players in a Sunday lineup, two tight ends, a lineup entered twice into a
single-entry contest, a must-play player placed by the builder and then
silently un-placed by the next step that ran.

## Data

`data/` holds a snapshot checked into the repo: DraftKings' public salary
export for the slate, contest details transcribed from the lobby, and
per-player scoring spread (average and 80th percentile) derived from
historical box scores. These are refreshed by hand, by re-running the
exporting scripts and committing the result — the app itself does not reach
out for them.

One thing IS fetched live, at runtime, every time the page loads: the injury
wire (Sleeper's public API). Two more files sit alongside the salary snapshot
and are refreshed the same way — by hand, weekly, via `refresh-week.sh` — but
from a live source rather than a one-time transcription: this season's own
snap/usage data and this season's own defence-vs-position table (both
nflverse, both real games actually played, not a bought preseason snapshot).
None of this changes the projection, which is still DraftKings' own
`AvgPointsPerGame` blended with last season — a backward-looking number, not a
forecast. What it does mean: the app is no longer blind to role, injury or
matchup the way an earlier version of this README said it was. The board
says exactly what each column is and is not, in its own captions, rather than
here.

## Layout

```
app.py              five tabs; rendering only
fv/rules.py         every measured constant, with the result that set it
fv/slate.py         which games a contest actually contains
fv/pool.py          salary export -> players
fv/blend.py         this season's games blended with last season's average
fv/optimize.py      stacked lineup construction, the leverage dial, must-play
fv/roster.py        DK slot order, stack labelling, CSV export
fv/entry.py         budget -> contests, by rake
fv/select.py        which contests to enter, and how many of each
fv/shape.py         classify a contest by its payout shape
fv/entered.py       the record of what has actually been entered
fv/injuries.py      the live injury wire, and who benefits when it changes
fv/mispricing.py    which players has DraftKings mispriced
fv/ownership.py     estimate what share of the field drafts each player
fv/matchup.py       defence vs position, from this season's own games
fv/kalshi.py        what the Kalshi prop markets imply, in DK points
fv/partner.py       a partner's players, so lineups do not collide
fv/context.py       what is pushing a player's outlook up or down
fv/sources.py       which publication each article claim came from
```

This is a port. The research repo that produced the measurements — the backtest
harnesses, the strategy ledger, the negative results — is private.

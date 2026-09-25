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

Picks nine players under the $50,000 cap, builds ten lineups that differ from
each other, and suggests which tournaments to enter them in. It never enters a
contest for you.

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

Two slates of 2026 have been played and graded, from DraftKings' own contest
entry history export rather than transcribed screens.

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
python3 -m unittest test_rules -v
```

Nineteen tests, each named for the bug it prevents — Monday-night players in a
Sunday lineup, two tight ends, a lineup entered twice into a single-entry
contest.

## Data

`data/` holds a snapshot: DraftKings' public salary export for the slate,
contest details transcribed from the lobby, and per-player scoring spread
(average and 80th percentile) derived from historical box scores. The app does
not fetch anything at runtime; refresh the snapshot to change slates.

The projections are DraftKings' own `AvgPointsPerGame` — a backward-looking
season average, not a forecast, with no matchup, role or injury information in
it. The app says so on screen.

## Layout

```
app.py            four tabs; rendering only
fv/rules.py       every measured constant, with the result that set it
fv/slate.py       which games a contest actually contains
fv/pool.py        salary export -> players
fv/optimize.py    stacked lineup construction
fv/roster.py      DK slot order, stack labelling, CSV export
fv/entry.py       budget -> contests, by rake
```

This is a port. The research repo that produced the measurements — the backtest
harnesses, the strategy ledger, the negative results — is private.

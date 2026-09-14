# Fourth & Value

A DraftKings NFL Classic lineup builder for large-field tournaments, and an
honest record of how well it works.

**[Open the app](https://share.streamlit.io)** · built with Streamlit

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

Week 1 of 2026 was the first slate this tool advised on and then saw graded. Ten
entries, $105 staked, nine of them scored.

| | |
|---|---|
| Mean finishing percentile | **61.3%** (95% CI 42.6–79.9) |
| Cashed | 2 of 9, against 23.1% of places paid |
| Return | $90 on $100 staked, **−10%** |

A player with no edge finishes at the 50th percentile, so 61.3% reads badly —
and the interval is 37 points wide, which is what nine entries on one slate buys
you. It is not evidence of anything. It is recorded because it is the account
rather than a backtest, and because this project reports its bad numbers in the
same place as its good ones.

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

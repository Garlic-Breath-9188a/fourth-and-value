"""
Fourth & Value — a DraftKings NFL Classic lineup builder for large-field tournaments.

The Streamlit port. The logic lives in fv/, ported from the TypeScript app in
../app, which remains the source of record because it holds the test harnesses
that produced every number quoted here.
"""
from __future__ import annotations
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from fv import rules
from fv.pool import (load_salaries, keep_starting_quarterbacks, apply_ceilings,
                     rosterable, load_json)
from fv.slate import slate_options, main_slate, restrict
from fv.optimize import build_portfolio, projection
from fv.roster import order_roster, stack_role, is_stacked, to_dk_csv
from fv.entry import plan, rake_pct

DATA = Path(__file__).resolve().parent / "data"

st.set_page_config(page_title="Fourth & Value", page_icon="🏈", layout="wide")

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 1600px; }
  .lu { border:1px solid rgba(128,128,128,.28); border-radius:8px; padding:.55rem .6rem; height:100%; }
  .lu h4 { margin:0 0 .35rem 0; font-size:.82rem; letter-spacing:.03em; opacity:.75; }
  .row { display:flex; justify-content:space-between; gap:.4rem; font-size:.83rem;
         padding:.14rem 0; border-bottom:1px solid rgba(128,128,128,.10); }
  .slot { width:2.6rem; flex:none; opacity:.55; font-size:.72rem; padding-top:.1rem; }
  .nm { flex:1 1 auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sal { flex:none; opacity:.6; font-variant-numeric:tabular-nums; }
  .qb   { color:#e8b53a; font-weight:600; }
  .st   { color:#e8b53a; }
  .bb   { color:#4da3ff; }
  .foot { display:flex; justify-content:space-between; font-size:.76rem;
          opacity:.7; padding-top:.4rem; }
</style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- data
@st.cache_data(show_spinner=False)
def load_pool():
    rows = load_salaries((DATA / "DKSalaries.csv").read_text())
    rows = keep_starting_quarterbacks(rows)
    rows = [r for r in rows if rosterable(r)]
    return apply_ceilings(rows, load_json(DATA / "player-variance.json"))


@st.cache_data(show_spinner=False)
def load_lobby():
    return json.loads((DATA / "lobby-week1-2026.json").read_text())


@st.cache_data(show_spinner=True)
def portfolio(ids, count, seed, ceiling_weight, qb_exposure, locked):
    pool = [p for p in load_pool() if p["id"] in set(ids)]
    return build_portfolio(pool, count, seed=seed, ceiling_weight=ceiling_weight,
                           qb_exposure=qb_exposure, locked=set(locked))


all_rows = load_pool()
options = slate_options(all_rows)
lobby = load_lobby()

# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### Slate")
    labels = [f"{s.label} — {s.game_count} games" for s in options]
    default = options.index(main_slate(options)) if main_slate(options) in options else 0
    pick = st.selectbox("Contest slate", labels, index=default,
                        help="Most tournaments are the Sunday main slate. A lineup built "
                             "for the wrong slate cannot be entered.")
    slate = options[labels.index(pick)]
    pool, dropped = restrict(all_rows, slate)

    st.markdown("### Portfolio")
    n_lineups = st.slider("Lineups", 1, 20, 10)
    budget = st.number_input("Weekly budget ($)", 5, 500, 40, step=5)
    qb_exposure = st.slider("Max one QB may appear (%)", 10, 100, rules.QB_EXPOSURE_PCT, step=10,
                            help="Tighter caps were measured over 101 weeks and did not "
                                 "help — P(180+) fell from 1.12% to 0.83% at 20%.")
    ceiling_weight = st.slider("Ceiling weight", 0.0, 1.0, 0.25, 0.05,
                               help="0 chases the projection, 1 chases the upside.")
    seed = st.number_input("Seed", 1, 9999, 1, help="Same seed, same lineups.")

    st.markdown("### Must-play")
    names = {f'{p["name"]} — {p["position"]} ${p["salary"]:,}': p["id"]
             for p in sorted(pool, key=lambda p: -p["projection"])}
    forced = st.multiselect("Include somewhere", list(names), max_selections=8,
                            help="Each of these appears in at least one lineup.")
    locked = tuple(names[n] for n in forced)

st.title("Fourth & Value")
st.caption(f"{slate.label} · {slate.game_count} games · {len(pool)} players")

tab_board, tab_pool, tab_entry, tab_about = st.tabs(
    ["Lineups", "Player pool", "Entry plan", "About"])

lineups = portfolio(tuple(p["id"] for p in pool), n_lineups, seed,
                    ceiling_weight, qb_exposure, locked)

# --------------------------------------------------------------------------- board
with tab_board:
    if dropped:
        st.info(f"Not on this slate, so excluded: {', '.join(dropped)}")
    if not lineups:
        st.error("No lineups could be built. Loosen the must-play list.")
    else:
        unplaced = [n for n in forced
                    if names[n] not in {p["id"] for l in lineups for p in l}]
        if unplaced:
            st.warning("Could not place: " + ", ".join(unplaced))

        st.markdown(
            '<span class="qb">■</span> QB &nbsp; <span class="st">■</span> his receiver '
            '&nbsp; <span class="bb">■</span> bring-back (other side of the same game)',
            unsafe_allow_html=True)

        per_row = 5
        for start in range(0, len(lineups), per_row):
            for col, (i, l) in zip(st.columns(per_row, gap="small"),
                                   enumerate(lineups[start:start + per_row], start + 1)):
                rows_html = []
                for slot, p in order_roster(l):
                    role = stack_role(p, l)
                    cls = {"QB": "qb", "STACK": "st", "BRING-BACK": "bb"}.get(role, "")
                    rows_html.append(
                        f'<div class="row"><span class="slot">{slot}</span>'
                        f'<span class="nm {cls}">{p["name"]}</span>'
                        f'<span class="sal">{p["salary"] // 100 / 10:.1f}k</span></div>')
                salary = sum(p["salary"] for p in l)
                mark = "✓ stacked" if is_stacked(l) else "no stack"
                col.markdown(
                    f'<div class="lu"><h4>LINEUP {i}</h4>{"".join(rows_html)}'
                    f'<div class="foot"><span>{mark}</span>'
                    f'<span>${salary:,} · {projection(l):.1f} pts</span></div></div>',
                    unsafe_allow_html=True)
            st.write("")

        st.download_button("Download DraftKings CSV", to_dk_csv(lineups),
                           file_name="fourth-and-value.csv", mime="text/csv")
        st.caption("Projections here are DraftKings' AvgPointsPerGame — last season's "
                   "average, not a forecast. No matchup, role or injury information is in them.")

# --------------------------------------------------------------------------- pool
with tab_pool:
    used = {}
    for l in lineups:
        for p in l:
            used[p["id"]] = used.get(p["id"], 0) + 1
    df = pd.DataFrame([{
        "Pos": p["position"], "Player": p["name"], "Team": p["team"], "Opp": p["opponent"],
        "Salary": p["salary"], "Proj": round(p["projection"], 1),
        "Ceiling": round(p["ceiling"], 1),
        "Pts/$1k": round(p["projection"] / (p["salary"] / 1000), 2),
        "Lineups": used.get(p["id"], 0),
        "Ceiling from": p["ceiling_source"],
    } for p in pool]).sort_values(["Lineups", "Proj"], ascending=False)
    c1, c2 = st.columns([1, 3])
    pos = c1.multiselect("Position", ["QB", "RB", "WR", "TE", "DST"])
    if pos:
        df = df[df["Pos"].isin(pos)]
    c2.caption(f"{len(df)} players. Backup quarterbacks and anyone Out, Doubtful or on "
               f"injured reserve are already removed.")
    st.dataframe(df, width="stretch", hide_index=True, height=620)

# --------------------------------------------------------------------------- entry
with tab_entry:
    # The lobby file labels a contest by its lock time, so that is the join key
    # between "which slate did we build for" and "which contests can take it".
    lock_label = f"{slate.locks_at.strftime('%-m/%-d %-I:%M')}p"
    entries, notes = plan(lobby["contests"], budget, len(lineups), lock_label)
    for n in notes:
        st.warning(n)
    if entries:
        st.dataframe(pd.DataFrame([{
            "Lineup": i + 1, "Contest": e.contest["name"], "Fee": f"${e.fee:,.0f}",
            "Prizes": f"${e.contest['totalPrizes']:,}",
            "Rake": f"{rake_pct(e.contest)}%" if rake_pct(e.contest) is not None else "—",
            "Entry limit": e.contest["maxEntriesPerUser"],
        } for i, e in enumerate(entries)]), width="stretch", hide_index=True)
        st.metric("Total", f"${sum(e.fee for e in entries):,.0f} of ${budget:,.0f}")
    st.caption(
        "Rake is what DraftKings keeps, measured against a full field. Across this "
        "109-contest board $3–$5 contests keep 15.0% and $100+ keep 9.7% — buying up "
        "is the one lever here that costs nothing. It makes the hole shallower; it "
        "does not make it a profit.")
    st.caption(f"Contest data hand-transcribed from lobby screenshots ({lobby['captured']}). "
               "Prize pools and fees are stable; entry counts move.")

# --------------------------------------------------------------------------- about
with tab_about:
    st.markdown(f"""
### What this is

An attempt to build a DraftKings NFL lineup builder that has an edge, and an
honest record of how far that got.

It picks nine players under the $50,000 cap, builds ten of them that differ from
each other, and tells you which tournaments to put them in. It never enters a
contest for you.

### What it actually does

**Builds around a stack.** Every lineup is a quarterback, two of his own pass
catchers, and one receiver from the team he is playing against. That last piece
is the one that matters. Over 101 weeks and 202,000 test lineups it did not
raise the average score at all — but it nearly doubled the chance of the kind of
week that wins a tournament, from 0.13% to 0.25%. A stack does not make a lineup
better. It makes it *swingier*, and a tournament only pays the top end.

**Keeps the tight end out of the FLEX.** Measured over 100 weeks: an extra
running back or receiver there beats an extra tight end on the chance of a big
week by about a third, and on the average score as well.

**Uses each player's own scoring history for his upside**, not a fixed multiple.
A flat multiple ranks everyone in a position identically, so it can't tell a
boom-or-bust receiver from a steady one. Players without enough history are
labelled in the pool table rather than given an invented number.

**Only builds from games in the contest you're entering.** Week 1 runs Wednesday
to Monday, but most tournaments are Sunday afternoon only.

**Drops players who can't play**, and drops backup quarterbacks — a backup priced
on last season's starts is the best points-per-dollar quarterback on any board,
and nothing else in the model can see that he isn't playing.

**Buys up where the budget allows.** Cheap contests are the expensive ones.

### What it does not do

The honest part. About twenty ideas have been tested against past seasons here,
and **one** of them beat simply picking the highest-projected legal lineup.
Simulation-driven selection: no better. Stars-and-scrubs: worse. Saving money at
the cheap slots: the two cheapest players are 11% of a lineup's swing, so it's
the wrong place to look.

The projections in this build are DraftKings' own season averages — backward
looking, with no matchup or injury information in them.

And the measurement that outranks all of the above: **an experienced player's
real account, 571 entries over two seasons, finished at the 49.1st percentile.**
Dead average. His headline was +467% ROI, but 85% of it came from a single
third-place finish out of 108,537 entries. Take that one week out and he returns
−14%, which is almost exactly what the rake alone predicts.

### So why use it

Because the rake is real and knowable, the roster rules are real and knowable,
and the stack effect is the one thing here that measured a genuine gain. That's
what it is. It is not a profit.

---
*{rules.NO_EDGE_WARNING}*
""")

"""
Fourth & Value — a DraftKings NFL Classic lineup builder for large-field tournaments.

Rendering only. Every decision lives in fv/, where it can be tested, and every
measured constant is in fv/rules.py next to the result that set it.
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
  .block-container { padding-top: 2.2rem; max-width: 1900px; }
  .lu { border:1px solid rgba(128,128,128,.28); border-radius:8px; padding:.55rem .6rem; height:100%; }
  .lu h4 { margin:0; font-size:.82rem; letter-spacing:.03em; opacity:.75; }
  .stk { font-size:.72rem; opacity:.65; margin:.1rem 0 .35rem 0; }
  /* Fixed columns are kept as narrow as the content allows, because the name
     is the only field a person actually reads and it is the one that clips. */
  .row { display:flex; align-items:baseline; gap:.3rem; font-size:.82rem;
         padding:.14rem 0; border-bottom:1px solid rgba(128,128,128,.10); }
  .slot { width:2.05rem; flex:none; opacity:.55; font-size:.66rem; }
  .nm { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
        white-space:nowrap; }
  .tm { flex:none; opacity:.5; font-size:.64rem; width:1.7rem; text-align:right; }
  .sal { flex:none; opacity:.6; font-size:.7rem; font-variant-numeric:tabular-nums;
         width:1.9rem; text-align:right; }
  .qb { color:#e8b53a; font-weight:600; }
  .st { color:#e8b53a; }
  .bb { color:#4da3ff; }
  .must { color:#ff4b4b; }
  .foot { display:flex; justify-content:space-between; font-size:.76rem;
          opacity:.7; padding-top:.4rem; }
  .cst { font-size:.73rem; opacity:.8; padding-top:.3rem;
         border-top:1px solid rgba(128,128,128,.18); margin-top:.3rem; }
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_pool():
    rows = load_salaries((DATA / "DKSalaries.csv").read_text())
    rows = keep_starting_quarterbacks(rows)
    rows = [r for r in rows if rosterable(r)]
    return apply_ceilings(rows, load_json(DATA / "player-variance.json"))


@st.cache_data(show_spinner=False)
def load_lobby():
    return json.loads((DATA / "lobby-week1-2026.json").read_text())


@st.cache_data(show_spinner=False)
def load_strategy():
    return json.loads((DATA / "strategy.json").read_text())


@st.cache_data(show_spinner=True)
def portfolio(ids, count, seed, ceiling_weight, qb_exposure, must_play):
    pool = [p for p in load_pool() if p["id"] in set(ids)]
    return build_portfolio(pool, count, seed=seed, ceiling_weight=ceiling_weight,
                           qb_exposure=qb_exposure, must_play=set(must_play))


all_rows = load_pool()
options = slate_options(all_rows)
lobby = load_lobby()

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
                            help="Tighter caps were measured over 101 weeks and did not help — "
                                 "the chance of a big week fell from 1.12% to 0.83% at 20%.")
    ceiling_weight = st.slider("Ceiling weight", 0.0, 1.0, 0.25, 0.05,
                               help="0 chases each player's expected score; 1 chases his best-case "
                                    "score, favouring boom-or-bust players. Whether moving this "
                                    "helps is unmeasured.")
    seed = st.number_input("Seed", 1, 9999, 1,
                           help="Lineups are drawn at random from the good ones. The seed fixes "
                                "that randomness: same seed gives the same ten lineups every "
                                "time, a different seed gives a different ten. Change it to see "
                                "alternatives; keep it to reproduce what you entered.")

    st.markdown("### Must-play")
    names = {f'{p["name"]} — {p["position"]} {p["team"]} ${p["salary"]:,}': p["id"]
             for p in sorted(pool, key=lambda p: -p["projection"])}
    forced = st.multiselect("Include somewhere", list(names), max_selections=8,
                            help="Each appears in at least one lineup — not all of them.")
    must_play = tuple(names[n] for n in forced)

st.title("Fourth & Value")
st.caption(f"{slate.label} · {slate.game_count} games · {len(pool)} players")

tab_board, tab_pool, tab_entry, tab_strategy, tab_about = st.tabs(
    ["Lineups", "Player pool", "Entry plan", "Strategy", "About"])

lineups = portfolio(tuple(p["id"] for p in pool), n_lineups, seed,
                    ceiling_weight, qb_exposure, must_play)
lock_label = f"{slate.locks_at.strftime('%-m/%-d %-I:%M')}p"
entries, entry_notes = plan(lobby["contests"], budget, len(lineups), lock_label)

with tab_board:
    if dropped:
        st.info(f"Not on this slate, so excluded: {', '.join(dropped)}")
    if not lineups:
        st.error("No lineups could be built. Loosen the must-play list.")
    else:
        placed = {p["id"] for l in lineups for p in l}
        unplaced = [n for n in forced if names[n] not in placed]
        if unplaced:
            st.warning("Could not fit into any lineup: " + ", ".join(unplaced))

        st.markdown(
            '<span class="qb">■</span> QB &nbsp; <span class="st">■</span> his receiver '
            '&nbsp; <span class="bb">■</span> bring-back &nbsp; '
            '<span class="must">●</span> must-play', unsafe_allow_html=True)

        per_row = 5
        for start in range(0, len(lineups), per_row):
            for col, (i, l) in zip(st.columns(per_row, gap="small"),
                                   enumerate(lineups[start:start + per_row], start + 1)):
                qb = next(p for p in l if p["position"] == "QB")
                body = []
                for slot, p in order_roster(l):
                    role = stack_role(p, l)
                    cls = {"QB": "qb", "STACK": "st", "BRING-BACK": "bb"}.get(role, "")
                    dot = '<span class="must">●</span> ' if p["id"] in must_play else ""
                    body.append(
                        f'<div class="row"><span class="slot">{slot}</span>'
                        f'<span class="nm {cls}">{dot}{p["name"]}</span>'
                        f'<span class="tm">{p["team"]}</span>'
                        f'<span class="sal">{p["salary"] // 100 / 10:.1f}k</span></div>')
                entry = entries[i - 1] if i - 1 < len(entries) else None
                contest = (f'<div class="cst">{entry.contest["name"]} · ${entry.fee:,.0f}</div>'
                           if entry else '<div class="cst">no contest — budget spent</div>')
                col.markdown(
                    f'<div class="lu"><h4>LINEUP {i}</h4>'
                    f'<div class="stk">{qb["team"]} stack · bring-back from {qb["opponent"]}</div>'
                    f'{"".join(body)}'
                    f'<div class="foot"><span>{"✓ stacked" if is_stacked(l) else "no stack"}</span>'
                    f'<span>${sum(p["salary"] for p in l):,} · {projection(l):.1f} pts</span></div>'
                    f'{contest}</div>', unsafe_allow_html=True)
            st.write("")

        spent = sum(e.fee for e in entries)
        c1, c2, c3 = st.columns(3)
        c1.metric("Entry fees", f"${spent:,.0f}", f"${budget - spent:,.0f} unspent"
                  if budget - spent else "budget fully used")
        c2.metric("Lineups", f"{len(lineups)}", f"{sum(map(is_stacked, lineups))} stacked")
        c3.metric("Contests", f"{len({e.contest['id'] for e in entries})}")
        for n in entry_notes:
            st.caption(f"⚠️ {n}")

        st.download_button("Download DraftKings CSV", to_dk_csv(lineups),
                           file_name="fourth-and-value.csv", mime="text/csv")
        st.caption("Projections here are DraftKings' AvgPointsPerGame — last season's average, "
                   "not a forecast. No matchup, role or injury information is in them.")

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

with tab_entry:
    for n in entry_notes:
        st.warning(n)
    if entries:
        st.dataframe(pd.DataFrame([{
            "Lineup": i + 1, "Contest": e.contest["name"], "Fee": f"${e.fee:,.0f}",
            "Prizes": f"${e.contest['totalPrizes']:,}",
            "Rake": f"{rake_pct(e.contest)}%" if rake_pct(e.contest) is not None else "—",
            "Limit": e.contest["maxEntriesPerUser"],
            "Why this one": e.reason,
        } for i, e in enumerate(entries)]), width="stretch", hide_index=True)
        st.metric("Total", f"${sum(e.fee for e in entries):,.0f} of ${budget:,.0f}")
    st.caption(
        "Rake is what DraftKings keeps, measured against a full field. Across this "
        "109-contest board $3–$5 contests keep 15.0% and $100+ keep 9.7% — buying up is the "
        "one lever here that costs nothing. It makes the hole shallower; it does not make "
        "it a profit.")
    st.caption(f"Contest data hand-transcribed from lobby screenshots ({lobby['captured']}). "
               "Prize pools and fees are stable; entry counts move.")

with tab_strategy:
    S = load_strategy()
    st.markdown("### Every idea this project tested, and what happened")
    st.caption("This exists because the expensive mistake is re-implementing something that "
               "was already measured and rejected. A null result stays on the list — a deleted "
               "row cannot stop the idea being retried.")

    counts = {}
    for r in S["ledger"]:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    cols = st.columns(len(S["verdictOrder"]))
    for col, v in zip(cols, S["verdictOrder"]):
        col.metric(S["verdictLabel"][v], counts.get(v, 0))

    st.dataframe(pd.DataFrame([{
        "Verdict": S["verdictLabel"][r["verdict"]],
        "Area": S["domainLabel"][r["domain"]],
        "Idea": r["claim"],
        "What it means": r["plain"],
        "Measurement": r["result"],
        "Sample": r["sample"],
    } for r in sorted(S["ledger"], key=lambda r: S["verdictOrder"].index(r["verdict"]))]),
        width="stretch", hide_index=True, height=420)

    with st.expander("What the verdicts mean"):
        for v in S["verdictOrder"]:
            st.markdown(f"**{S['verdictLabel'][v]}** — {S['verdictMeaning'][v]}")

    st.markdown("### Claims from the strategy articles")
    st.caption(f"{len(S['claims'])} individual claims pulled out of published DFS strategy "
               f"writing, measured over {S['slatesMeasured']} slates as residuals against "
               f"DraftKings salary — i.e. does the price already account for it. One standard "
               f"deviation is about {S['pointsPerSd']} DK points.")
    st.warning("**Do not add these up.** The same effect appears in several rows measured on "
               "different subsets, volume traits select overlapping players, and no nine players "
               "can be at home, indoors, high-volume and in good matchups at once. Measured at "
               "the lineup level, home and dome together are worth +1.55 points, against ~3.6 if "
               "they simply added across a roster.")
    def claim_row(c):
        ci = c.get("ci") or []
        effect = c.get("effect")
        return {
            "Status": S["statusLabel"][c["status"]],
            "Group": S["groupLabel"].get(c.get("group", ""), c.get("group", "")),
            "Claim": c["claim"],
            "Effect (SD)": effect,
            "In DK points": round(effect * S["pointsPerSd"], 2) if effect is not None else None,
            "95% CI": f"{ci[0]}…{ci[1]}" if len(ci) == 2 else "",
            "Slates": c.get("slates"),
            # A modelled total or spread is a weaker test than real Vegas lines,
            # so a null from one is weak evidence rather than a settled answer.
            "Proxy?": "modelled" if c.get("modelled") else "",
            "Articles": c.get("articles", ""),
            "Note": c.get("note", ""),
        }

    st.dataframe(pd.DataFrame([claim_row(c) for c in sorted(
        S["claims"], key=lambda c: (S["statusOrder"].index(c["status"]),
                                    -abs(c.get("effect") or 0)))]),
        width="stretch", hide_index=True, height=420)

    with st.expander("What the statuses mean"):
        for v in S["statusOrder"]:
            st.markdown(f"**{S['statusLabel'][v]}** — {S['statusMeaning'][v]}")

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
and **one** of them beat simply picking the highest-projected legal lineup. The
full list is on the Strategy tab, failures included.

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

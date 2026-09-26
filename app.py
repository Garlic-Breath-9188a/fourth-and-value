"""
Fourth & Value — a DraftKings NFL Classic lineup builder for large-field tournaments.

Rendering only. Every decision lives in fv/, where it can be tested, and every
measured constant is in fv/rules.py next to the result that set it.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from fv import rules, context, injuries, sources, entered as ent
from fv.pool import (load_salaries, keep_starting_quarterbacks, apply_ceilings,
                     rosterable, load_json)
from fv.slate import slate_options, main_slate, restrict
from fv.optimize import build_portfolio, improve_portfolio, projection
from fv.roster import order_roster, stack_role, is_stacked, to_dk_csv, fill_dk_template
from fv.entry import plan, rake_pct, playable
from fv import blend as blend_mod
from fv import select as select_mod
from fv import mispricing as mispricing_mod
from fv import kalshi as kalshi_mod

DATA = Path(__file__).resolve().parent / "data"

def _captured(key: str) -> str:
    """
    When a shipped data file was actually captured from its source.

    Recorded in data/manifest.json rather than read from the file's modification
    time: mtime resets whenever a file is copied, so the first version of this
    reported the day the app was deployed as the day the slate was pulled --
    a freshness claim that was wrong by a week and looked authoritative.
    """
    try:
        return json.loads((DATA / "manifest.json").read_text())[key]["captured"]
    except Exception:
        return "unknown"


def _data_version() -> str:
    """
    A digest of every shipped data file, used as a cache key.

    The parameter it feeds must NOT be named with a leading underscore:
    Streamlit excludes those from the hash -- they are the escape hatch for
    unhashable arguments -- so `_version` would have made this whole mechanism
    do nothing, silently and convincingly.

    Streamlit caches these loaders on their arguments, and they had none -- so a
    new salary file did not invalidate anything and the app went on serving the
    previous week's slate from cache. The header read "Sun 9/13" against a 9/20
    export, which looks like a data problem and is not one.

    Passing this in as an argument means the cache turns over exactly when the
    data does, without anyone having to remember to clear it.
    """
    h = hashlib.sha256()
    for f in sorted(DATA.glob("*")):
        if f.is_file():
            h.update(f.name.encode())
            h.update(str(f.stat().st_size).encode())
            h.update(str(int(f.stat().st_mtime)).encode())
    return h.hexdigest()[:12]


def _manifest(key: str) -> dict:
    """The whole manifest entry, for the week number and the notes."""
    try:
        return json.loads((DATA / "manifest.json").read_text()).get(key, {})
    except Exception:
        return {}

st.set_page_config(page_title="Fourth & Value", page_icon="🏈", layout="wide")

st.markdown("""
<style>
  /* Freshness strip: one line, small, red where something predates the slate. */
  .fresh { display:flex; flex-wrap:wrap; gap:.35rem .9rem; align-items:baseline;
           margin:-.3rem 0 .5rem; padding:.3rem .55rem; border-radius:6px;
           background:rgba(128,128,128,.07); font-size:.72rem; line-height:1.25; }
  .fresh .fi { display:inline-flex; align-items:baseline; gap:.32rem; white-space:nowrap; }
  .fresh .fi b { font-weight:600; opacity:.75; }
  .fresh .fi i { font-style:normal; font-variant-numeric:tabular-nums; }
  .fresh .fi u { text-decoration:none; opacity:.55; }
  .fresh .fi.bad i { color:#d33; font-weight:700; }
  .fresh .fi.bad b { color:#d33; }

  .block-container { padding-top: 2.2rem; max-width: 1900px; }
  .lu { border:1px solid rgba(128,128,128,.28); border-radius:8px; height:100%;
        overflow:hidden; }
  /* The heading bar is painted, not tinted, so it reads the same in either
     theme -- a translucent bar over a dark background loses its contrast. */
  .bar { background:#1f2430; color:#ffffff; font-weight:700; font-size:.8rem;
         letter-spacing:.05em; padding:.34rem .6rem; }
  .body { padding:.4rem .6rem .55rem .6rem; }
  .cst { font-size:.73rem; opacity:.85; text-align:right; padding:.05rem 0 .12rem 0;
         overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .stk { font-size:.72rem; opacity:.7; margin:0 0 .35rem 0; }
  .tick { color:#3ecf6e; font-weight:700; }
  .notick { color:#c9713a; }
  /* Fixed columns are kept as narrow as the content allows, because the name
     is the only field a person actually reads and it is the one that clips. */
  .row { display:flex; align-items:baseline; gap:.3rem; font-size:.82rem;
         padding:.14rem 0; border-bottom:1px solid rgba(128,128,128,.10); }
  .slot { width:2.1rem; flex:none; opacity:.55; font-size:.7rem; }
  .nm { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
        white-space:nowrap; }
  .tm { flex:none; opacity:.72; font-size:.76rem; width:2.1rem; text-align:right; }
  .sal { flex:none; opacity:.75; font-size:.78rem; font-variant-numeric:tabular-nums;
         width:2.3rem; text-align:right; }
  .qb { color:#e8b53a; font-weight:600; }
  .st { color:#e8b53a; }
  .bb { color:#4da3ff; }
  .must { color:#ff4b4b; }
  .foot { display:flex; justify-content:space-between; font-size:.76rem;
          opacity:.7; padding-top:.4rem; }
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_pool(version: str):
    """
    The raw rosterable pool, WITHOUT ceilings.

    Ceilings are a multiple of the projection, so applying them here would
    compute them from DraftKings' unblended average -- in week 2 that is one
    game. Use `prepared_pool`, which blends first and then derives the ceiling
    from the blended number.
    """
    rows = load_salaries((DATA / "DKSalaries.csv").read_text())
    rows = keep_starting_quarterbacks(rows)
    return [r for r in rows if rosterable(r)]


def prepared_pool(rows: list[dict], week: int) -> list[dict]:
    """
    Blend, THEN derive ceilings. The order is the whole point.

    `apply_ceilings` sets ceiling = projection x the player's own spread ratio,
    so running it before the blend leaves every ceiling scaled to the number the
    blend exists to correct. With the ceiling weight at 1.0 the builder leans on
    that number entirely, which would quietly undo the blend.

    This was a real defect, not a hypothetical: `portfolio()` rebuilt its pool
    from `load_pool` and never blended at all, so the board displayed blended
    projections while the lineups were constructed from DraftKings' raw
    averages. On the Week 2 2026 slate the shipped lineups scored 151.5 on the
    blend against 162.8 for lineups actually built on it, sharing 3 of 9
    players.
    """
    blended = blend_mod.apply_blend([dict(r) for r in rows],
                                    load_prior_season(_data_version()), int(week))
    return apply_ceilings(blended, load_json(DATA / "player-variance.json"))


@st.cache_data(show_spinner=False)
def load_prior_season(version: str):
    """Last season's scoring, for blending against a thin in-season average."""
    return blend_mod.load_prior(DATA / "prior-season-2025.json")


@st.cache_data(show_spinner=False)
def load_usage(version: str):
    """
    This season's snap share and touches, for the mispricing board.

    Absent is a supported state, not an error: before the season's first games
    there is nothing to load, and `mispricing.board` returns everyone unranked
    with the reason rather than an empty page.
    """
    return load_json(DATA / "usage-2026.json")


@st.cache_data(show_spinner=False)
def load_lobby(version: str):
    """
    The most recent lobby transcription on disk.

    Chosen by the week number in the filename rather than hardcoded, because a
    hardcoded `lobby-week2-2026.json` means every new slate needs a code edit to
    go live -- and the failure is silent: the app keeps serving last week's
    contests against this week's players, which looks like a data problem and is
    not one. Older files stay for the record.
    """
    files = sorted(DATA.glob("lobby-week*-*.json"),
                   key=lambda f: int(f.stem.split("-")[1].replace("week", "")))
    if not files:
        return {"contests": [], "note": "no lobby file on disk"}
    board = json.loads(files[-1].read_text())

    # Join the verified payout curves on. Without this the lobby carries no
    # topPrizeMultiple, the contest scorer reports "top prize unknown" for every
    # contest, and its second-largest weight (W_TOP = 0.55) does nothing at all.
    #
    # That is not academic. On the Week 3 board it left the three Red Zone
    # contests -- same $50 fee, same 12.0% rake, same single-entry -- separated
    # only by an overlay figure from a Friday-night capture, and chose the $100K
    # (200x top prize) over the $200K (500x). For a tournament that is the wrong
    # way round, and the number that would have said so was sitting in
    # payouts.json unread.
    curves = load_json(DATA / "payouts.json") or {}
    for c in board.get("contests", []):
        cur = curves.get(c["name"])
        if not cur:
            continue
        for field in ("topPrizeMultiple", "cashRate", "minCashMultiple", "paidPlaces"):
            if cur.get(field) is not None:
                c.setdefault(field, cur[field])
    return board


@st.cache_data(ttl=21600, show_spinner="Checking the injury wire…")
def live_injuries():
    """
    Cached for six hours. Sleeper asks callers not to pull the full player file
    more than once a day; six hours is well inside that and still catches a
    Friday practice report before a Sunday lock.
    """
    return injuries.index(injuries.fetch())


@st.cache_data(show_spinner=False)
def load_open_board(version: str):
    """
    The CURRENT contest board. Deliberately the same object the rest of the page
    uses, not a second file.

    This used to read `contests-open-0913.json`, captured on 13 September, and
    render its 37 contests under the heading "Still open". They were Week 1
    contests; every one of them had locked a fortnight earlier. The $400K
    Bootleg was being offered as an open seat two slates after it finished.

    A second file describing the same thing as the lobby is a second thing to
    keep fresh, and the one nobody refreshes is the one that lies. There is now
    one board.
    """
    return load_lobby(version)


@st.cache_data(show_spinner=False)
def load_strategy(version: str):
    return json.loads((DATA / "strategy.json").read_text())


@st.cache_data(show_spinner=False)
def load_placed_entries(version: str):
    """
    Entries actually placed on DraftKings, transcribed from the entry screen.

    Takes `version` for the same reason every other loader here does: a cached
    function with NO arguments has a constant cache key and never turns over, so
    rewriting entries-placed.json changed nothing on screen. That is the same
    defect as the leading-underscore one this file already warns about, and it
    hid six freshly recorded entries.

    Ships with the app. An earlier version kept this out of the repository and
    offered a file uploader instead, on the grounds that the repo is public --
    which was the wrong trade: it made the user do setup on every visit to see
    his own data. The record is 7 lineups and $25 of entry fees, and DraftKings
    publishes every contest's entries and lineups after lock anyway.
    """
    return ent.load_placed(DATA / "entries-placed.json")


@st.cache_data(show_spinner=True)
def portfolio(ids, count, seed, ceiling_weight, qb_exposure, rb_exposure, must_play,
              require_wr1, avoid_keys, week, other_exposure, exclude_ids):
    """
    `avoid_keys` holds the rosters already entered. They are built extra and
    then filtered out, rather than being forbidden during construction, so the
    board still comes back full after you have entered several.

    `week` is an argument rather than a global because it drives the blend, and
    the blend must be inside this function: rebuilding the pool from `load_pool`
    and forgetting it is exactly the bug this signature replaces.
    """
    pool = prepared_pool([p for p in load_pool(_data_version()) if p["id"] in set(ids)], week)
    if exclude_ids:
        pool = [p for p in pool if p["id"] not in set(exclude_ids)]
    avoid = set(avoid_keys)
    want = count + len(avoid)
    built = build_portfolio(pool, want, seed=seed, ceiling_weight=ceiling_weight,
                            qb_exposure=qb_exposure, rb_exposure=rb_exposure,
                            other_exposure=other_exposure,
                            must_play=set(must_play), require_wr1=require_wr1)
    # The sampler stops at whatever it happened to draw. This walks each lineup
    # for a swap that raises the projection, singly and then in pairs. On the
    # Week 3 board it moved the mean 152.1 -> 155.3 and spent $5,600 of idle
    # salary cap down to $600, in about a second, without breaking the stack,
    # the exposure caps or the distinctness of the ten.
    built = improve_portfolio(built, pool, qb_exposure, rb_exposure, other_exposure)
    fresh = [l for l in built if ent.roster_key(l) not in avoid]
    return fresh[:count]


if "entered" not in st.session_state:
    st.session_state.entered = {}
if "contests_done" not in st.session_state:
    st.session_state.contests_done = set()

all_rows = load_pool(_data_version())
options = slate_options(all_rows)
lobby = load_lobby(_data_version())

with st.sidebar:
    st.markdown("### Slate")
    labels = [f"{s.label} — {s.game_count} games" for s in options]
    default = options.index(main_slate(options)) if main_slate(options) in options else 0
    pick = st.selectbox("Contest slate", labels, index=default,
                        help="Most tournaments are the Sunday main slate. A lineup built "
                             "for the wrong slate cannot be entered.")
    slate = options[labels.index(pick)]
    # The dropped games are deliberately not shown: the user asked for what IS
    # on the slate, not a list of teams that are not. The slate label above
    # already says which window is in play.
    pool, _dropped = restrict(all_rows, slate)

    # ---- Blend a thin in-season average with last season --------------------
    # DraftKings' AvgPointsPerGame is one game in Week 2 and the optimizer
    # believes it completely: Bryce Young read 35.4 off a single Sunday against
    # a 2025 season of 14.9. Measured over 40 early-season slates, blending cuts
    # projection error from 6.09 to 5.55 MAE, and from 7.11 to 5.78 in week 2
    # specifically -- about twelve points across a roster.
    #
    # The week is inferred from the slate date, because the salary export does
    # not carry it. Editable, since a wrong week silently changes every
    # projection and that should be visible rather than buried.
    # The week comes from the manifest, which is written when the slate is
    # captured and therefore KNOWS it. The old heuristic counted weeks from
    # September 1st and was one too high EVERY week of the season -- 9/13 read
    # as week 2, 9/20 as week 3, 9/27 as week 4 -- because the season does not
    # start on the 1st. That is not a cosmetic error: at week 2 it put the blend
    # at 50% this season instead of 25%, which is half of the correction the
    # blend exists to make, in the direction of trusting one game too much.
    #
    # The date arithmetic survives only as a fallback for a board captured
    # without a week, and is anchored on the slate's own first Sunday rather
    # than on the calendar month.
    manifest_week = _manifest("slate").get("week")
    if manifest_week:
        guessed = int(manifest_week)
    else:
        first_sunday = datetime(slate.locks_at.year, 9, 1, tzinfo=slate.locks_at.tzinfo)
        while first_sunday.weekday() != 6:                    # 6 = Sunday
            first_sunday += timedelta(days=1)
        first_sunday += timedelta(days=7)                     # week 1 is the SECOND Sunday
        guessed = max(1, min(18, ((slate.locks_at - first_sunday).days // 7) + 1))
    nfl_week = st.number_input(
        "NFL week", 1, 18, int(guessed), key="nfl_week",
        help="Sets how much of the projection comes from this season rather than "
             "last. Week 1 uses DraftKings' number unchanged; week 2 is 25% this "
             "season and 75% of 2025, rising as games accumulate.")
    prior = load_prior_season(_data_version())
    # Same helper the builder uses, so what is displayed and what is constructed
    # can never disagree again.
    pool = prepared_pool(pool, int(nfl_week))
    blended = sum(1 for r in pool if "%" in str(r.get("projection_source", "")))
    if int(nfl_week) > 1:
        w = blend_mod.current_weight(blend_mod.games_played_before(int(nfl_week)))
        st.caption(
            f"Projections are **{w * 100:.0f}% this season, {(1 - w) * 100:.0f}% "
            f"{prior.get('season')}** — {blended} of {len(pool)} players matched. "
            "DraftKings' own average is one game in week 2, which the optimizer "
            "would otherwise take at face value.")

    st.markdown("### Portfolio")
    n_lineups = st.slider("Lineups", 1, 20, 10)
    budget = st.number_input("Weekly budget ($)", 5, 500, 100, step=5)
    qb_exposure = st.slider("Max one QB may appear (%)", 10, 100, rules.QB_EXPOSURE_PCT, step=10,
                            help="Tighter caps were measured over 101 weeks and did not help — "
                                 "the chance of a big week fell from 1.12% to 0.83% at 20%.")
    rb_exposure = st.slider("Max one RB may appear (%)", 10, 100, rules.RB_EXPOSURE_PCT, step=10,
                            help="Running backs are the most concentrated position in a "
                                 "portfolio because few are worth playing. A cap only does "
                                 "something while it BINDS: no back reached more than 6 of 10 "
                                 "lineups unprompted on this slate, so every setting above ~60% "
                                 "gives the same portfolio. Tighter caps were measured and cost "
                                 "the tail — P(180+) 1.12% → 0.83% at 20%.")
    other_exposure = st.slider("Max one WR/TE/DST may appear (%)", 10, 100,
                               rules.OTHER_EXPOSURE_PCT, step=10,
                               help="Receivers had no cap of their own and fell through to 75%, "
                                    "which at ten lineups is seven. One receiver appeared in 7 of "
                                    "10 lineups and then in three of four entries actually "
                                    "placed. A lineup holds three WRs plus usually the FLEX, so "
                                    "this is the position where an uncapped default does most "
                                    "damage.")
    avoid_used_qbs = st.checkbox(
        "Skip quarterbacks already entered", value=True,
        help="A quarterback in a lineup you have already staked is not offered again "
             "this week. Concentration protection, not a measured edge: tighter QB caps "
             "were measured to cost the tail, but a busted QB you leaned on takes the "
             "whole portfolio down 53% of the time against 32%.")
    ceiling_weight = st.slider("Ceiling weight", 0.0, 1.0, rules.CEILING_WEIGHT_DEFAULT, 0.05,
                               help="0 chases each player's expected score; 1 chases his best-case "
                                    "score, favouring boom-or-bust players. Every player here has "
                                    "a ceiling from his own scoring spread, so this genuinely "
                                    "reorders the board. Whether it helps is unmeasured.")
    seed = st.number_input("Seed", 1, 9999, 1,
                           help="Lineups are drawn at random from the good ones. The seed fixes "
                                "that randomness: same seed gives the same ten lineups every "
                                "time, a different seed gives a different ten. Change it to see "
                                "alternatives; keep it to reproduce what you entered.")

    st.markdown("### Stacking")
    require_wr1 = st.checkbox("Stack must include the QB's WR1", value=True,
                              help="WR1 is the team's highest-priced receiver. Measured over "
                                   "268 week-trials: no difference either way. Slightly worse on "
                                   "average and best-of-10, slightly better on the chance of a "
                                   "big week, none of the three clearing its interval. Nothing "
                                   "is lost by leaving it on; it is just not an edge.")

    st.markdown("### Injury wire")
    use_live = st.checkbox("Cross-check the salary file", value=True,
                           help="Pulls current injury status from Sleeper and removes anyone "
                                "the wire says cannot play, even if the salary file still "
                                "lists them as available.")

    st.markdown("### Must-play")
    names = {f'{p["name"]} — {p["position"]} {p["team"]} ${p["salary"]:,}': p["id"]
             for p in sorted(pool, key=lambda p: -p["projection"])}
    forced = st.multiselect("Include somewhere", list(names), max_selections=8,
                            help="Each appears in at least one lineup — not all of them.")
    must_play = tuple(names[n] for n in forced)

feed = live_injuries() if use_live else {}
conflicts = injuries.cross_check(pool, feed) if feed else []
blocked = {c["id"] for c in conflicts if c["live_status"] in injuries.BLOCKING}
if blocked:
    pool = [p for p in pool if p["id"] not in blocked]

st.title("Fourth & Value")
st.caption(f"{slate.label} · {slate.game_count} games · {len(pool)} players")

# A stale contest board is worse than a stale slate: the lineups are fine and the
# plan names tournaments that no longer exist. The header showed a Week 1 date
# beside a Week 2 slate and looked perfectly normal, so the comparison is made
# explicitly rather than left for the reader to notice.
_slate_meta = _manifest("slate")
_slate_week = _slate_meta.get("week")
_board_stale = _captured("contests") < _captured("slate") != "unknown"

# The freshness line lives on the Lineups tab, one compressed row. These two are
# not freshness notes -- an injury wire that is off or unreachable changes which
# players are eligible, so it stays loud and above the tabs.
if use_live and not feed:
    st.error("**Injury wire unreachable.** Falling back to the salary file's own Status "
             "column, which is frozen at export time.", icon="⚠️")
elif not use_live:
    st.warning("**Injury cross-check is off.** Availability is whatever the salary file said "
               "when it was exported.", icon="⚠️")

# ---- Injury designation, shared by every recommendation table --------------
# A questionable player is deliberately KEPT in the pool: the doubt is what
# suppresses his ownership, which is the point in a tournament. But a table that
# recommends him without saying he is hurt hides the one thing worth checking
# before rostering him. Adonai Mitchell sat at the top of the market watchlist,
# questionable with a finger injury, and nothing on screen said so.
#
# Defined here rather than inside a tab because the mispricing board renders on
# the Lineups tab and the market watchlist on the Player pool tab -- putting it
# in either one leaves the other with a NameError.
_status_by_name = {p["name"]: (p.get("status") or "Active") for p in pool}
_body_by_name = {}
if use_live and feed:
    for _c in injuries.cross_check(pool, feed):
        _body_by_name[_c["name"]] = _c.get("body_part") or ""
        _status_by_name[_c["name"]] = _c["live_status"]


def _flag(name: str) -> str:
    """"Questionable (Finger)" for a designated player, empty for a fit one."""
    st = _status_by_name.get(name, "Active")
    if st == "Active":
        return ""
    part = _body_by_name.get(name)
    return f"{st} ({part})" if part else st


tab_board, tab_pool, tab_entry, tab_strategy, tab_about = st.tabs(
    ["Lineups", "Player pool", "Entry plan", "Strategy", "About"])

# ---- Do not build another lineup around a quarterback already staked --------
# Asked for directly. One quarterback per week is a concentration choice rather
# than a measured edge -- tighter QB caps were measured to COST the tail
# (P(180+) 1.12% -> 0.83% at a 20% cap) -- but it buys the downside that was
# measured: when the quarterback a portfolio leans on busts, the chance every
# lineup fails rises from 32% to 53%.
used_qbs = ent.quarterbacks_used(load_placed_entries(_data_version()), st.session_state.entered)
build_ids = ent.without_used_quarterbacks(pool, used_qbs) if avoid_used_qbs else pool
if avoid_used_qbs and used_qbs and len(build_ids) < len(pool):
    gone = sorted({p["name"] for p in pool} - {p["name"] for p in build_ids})
    st.caption(f"Not building on {len(gone)} quarterback(s) already entered: "
               + ", ".join(gone) + ". Turn this off in the sidebar to reuse them.")
if avoid_used_qbs and not any(p["position"] == "QB" for p in build_ids):
    st.error("Every quarterback on this slate is already in an entered lineup. "
             "Turn off 'Skip quarterbacks already entered' in the sidebar to build more.",
             icon="⚠️")
    build_ids = pool

lineups = portfolio(tuple(p["id"] for p in build_ids), n_lineups, seed,
                    ceiling_weight, qb_exposure, rb_exposure, must_play, require_wr1,
                    tuple(sorted(st.session_state.entered)), int(nfl_week),
                    other_exposure, ())
lock_label = f"{slate.locks_at.strftime('%-m/%-d %-I:%M')}p"
entries, entry_notes = plan(lobby["contests"], budget, len(lineups), lock_label)

with tab_board:
    # ---- Where every input came from, and when -----------------------------
    # One compressed line rather than five metric cards: this is a check you
    # glance at, not a dashboard. Added after the header showed Week 1 dates
    # against a Week 2 slate and looked entirely normal -- a stale input is
    # invisible unless it is dated next to the thing it feeds.
    #
    # Last season is deliberately absent: it is a closed season and cannot go
    # stale, so dating it was noise.
    _pool_n = len(pool)
    _inj_n = len(feed) if (use_live and feed) else 0
    _usage = load_usage(_data_version())
    _kal = _manifest("kalshi")
    _kal_n = (_kal.get("note", "").split(" ")[0] if _kal.get("note", "")[:1].isdigit() else "?")
    _bits = [
        (f"Player pool{f' · wk {_slate_week}' if _slate_week else ''}",
         _captured("slate"), f"{_pool_n} players", False),
        ("Injury wire", f"{_inj_n} records" if _inj_n else "off",
         "live, 6-hourly" if _inj_n else "using the salary file", not _inj_n),
        ("Snap data", _captured("usage"),
         f"{len(_usage.get('players', {})) if _usage else 0} players"
         + (f" · thru wk {_usage.get('throughWeek')}" if _usage else ""),
         not _usage),
        ("Kalshi", _captured("kalshi"), f"{_kal_n} matched",
         _captured("kalshi") < _captured("slate")),
        ("Contests", _captured("contests"), f"{len(lobby['contests'])} listed",
         _board_stale),
    ]
    st.markdown(
        '<div class="fresh">' + "".join(
            f'<span class="fi{" bad" if warn else ""}">'
            f'<b>{label}</b><i>{when}</i><u>{extra}</u></span>'
            for label, when, extra, warn in _bits
        ) + "</div>", unsafe_allow_html=True)

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

        # ---- Contests you can still put a lineup into ---------------------
        # Filtered the same way the planner filters, plus each contest's own
        # per-user limit measured against what is ALREADY marked entered: a
        # [Single Entry] contest holding one of your lineups must not be offered
        # for a second, which is not a rounding error but a lineup DraftKings
        # will reject.
        open_contests = ent.contests_with_room(
            playable(lobby["contests"], lock_label, "main"), st.session_state.entered)
        # Dearest first. `contests_with_room` returns cheapest-first, which put
        # the $10 contest at the top of all ten dropdowns -- and since a lineup
        # with no assigned contest defaulted to index 0, six of ten cards read
        # "$50K 1st and 10" whether or not that was sensible.
        open_contests.sort(key=lambda c: (-c["entryFee"], -c.get("totalPrizes", 0)))

        # For a lineup the plan did not fund, default to the BEST remaining
        # contest by the same ranking the plan uses, not to whatever the sort
        # happens to put first. Position in a dropdown is not a recommendation.
        _rank = {s.contest["id"]: n
                 for n, s in enumerate(select_mod.ranked(lobby["contests"]))}
        _best_spare = min(range(len(open_contests)),
                          key=lambda n: _rank.get(open_contests[n]["id"], 10 ** 6),
                          default=0) if open_contests else 0

        def contest_label(c: dict) -> str:
            r = rake_pct(c)
            left = ent.entries_left(c, st.session_state.entered)
            cap = f" · {left} left" if c.get("maxEntriesPerUser", 1) > 1 else " · single"
            return f'${c["entryFee"]:,.0f} · {c["name"]}' + (f' · {r:.1f}% rake' if r is not None else "") + cap

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
                contest = (f'{entry.contest["name"]} · ${entry.fee:,.0f}'
                           if entry else 'not in the plan — pick one below')
                mark = ('<span class="tick">✓</span>' if is_stacked(l)
                        else '<span class="notick">✗</span>')
                col.markdown(
                    f'<div class="lu"><div class="bar">LINEUP {i}</div><div class="body">'
                    f'<div class="cst" title="{contest}">{contest}</div>'
                    f'<div class="stk">{mark} {qb["team"]} stack · bring-back from {qb["opponent"]}</div>'
                    f'{"".join(body)}'
                    f'<div class="foot"><span></span>'
                    f'<span>${sum(p["salary"] for p in l):,} · {projection(l):.1f} pts</span></div>'
                    f'</div></div>', unsafe_allow_html=True)
                # ---- Mark entered, against a contest you choose -----------
                # The checkbox used to record whatever contest the PLANNER had
                # assigned, and said "raise the budget first" when it had
                # assigned none -- so a lineup actually entered somewhere else
                # could not be recorded at all. The contest is now picked here,
                # defaulting to the plan's suggestion. Marking it removes the
                # lineup from the board on the next build (`avoid_keys`), so the
                # nine players are not offered to you twice.
                key = ent.roster_key(l)
                if ent.is_entered(st.session_state.entered, l):
                    # Defensive only: an entered roster is filtered out of the
                    # next build, so this branch should not render. Unmarking
                    # lives on the Entry plan tab, where the record still exists.
                    rec = st.session_state.entered[key]
                    col.success(f"entered · {rec['contest']} · ${rec['fee']:,.0f}", icon="✓")
                elif not open_contests:
                    col.caption("No contest on this slate has a known payout structure.")
                else:
                    suggested = _best_spare
                    if entry:
                        suggested = next((n for n, c in enumerate(open_contests)
                                          if c["id"] == entry.contest["id"]), _best_spare)
                    pick = col.selectbox(
                        "Contest", range(len(open_contests)), index=suggested,
                        format_func=lambda n: contest_label(open_contests[n]),
                        key=f"pick_{key}", label_visibility="collapsed")
                    if col.button("Mark entered", key=f"mark_{key}", width="stretch"):
                        chosen = open_contests[pick]
                        ent.record(st.session_state.entered, l, chosen, chosen["entryFee"])
                        st.rerun()
            st.write("")


        # ---- Where DraftKings has mispriced the slate -------------------
        # Measured in app/scripts/test-salary-mispricing.mjs; the numbers quoted
        # on screen are held out on 2024-2025 over 32 slates. Two things about it
        # are worth knowing before reading the table:
        #
        #   - The signal is ONLY among cheap players. Among expensive ones the
        #     held-out interval spans zero, so there is no "expensive bargain"
        #     list here and adding one would be inventing it.
        #   - DraftKings' own week-to-week price change predicts NOTHING
        #     (+0.013 SD, CI -0.023 to 0.049). That was the original hypothesis.
        #     It is printed below so it does not get tried again.
        mis = mispricing_mod.board(pool, _usage)
        M = mispricing_mod.MEASURED
        st.markdown("### Where DraftKings has mispriced this slate")
        if not mis["ranked"]:
            st.caption("No snap-share data loaded, so the board cannot be built. "
                       "Run `node scripts/fetch-usage-2026.mjs` in the app directory "
                       "and copy `usage-2026.json` into `streamlit/data/`.")
        else:
            st.caption(
                f"Last game's snap share and touches, against what each player's price "
                f"implies. Cheap players with a real role beat their price by "
                f"**{M['cheap_top']['points']:+.2f} DK points** "
                f"(95% CI {M['cheap_top']['ci'][0]:.3f} to {M['cheap_top']['ci'][1]:.3f} SD); "
                f"the projection this app already uses finds nothing there "
                f"({M['projection_cheap_top']['sd']:+.3f} SD, spans zero) and picks only "
                f"{M['overlap_with_projection']:.0%} of the same players. "
                f"Held out on {M['holdout']}, {M['slates']} slates."
            )
            def _frame(rows, limit=12):
                return pd.DataFrame([{
                    "Player": r["name"], "Pos": r["position"], "Team": r["team"],
                    "Status": _flag(r["name"]) or "—",
                    "Salary": f"${r['salary']:,.0f}",
                    "Snap": f"{r['snap_pct']:.0f}%",
                    "Touches": r["touches"],
                    "vs price": f"{r['edge_points']:+.2f}",
                    "Measured": "yes" if r["measured"] else "position unproven",
                } for r in rows[:limit]])

            # The FADE half of this board is not shown. It was measured -- 2 of
            # 84 flagged players reached 20+ points, a 2% hit rate against an 8%
            # base rate, with the biggest disagreements doing worst -- and a
            # refuted signal on a working screen is worse than no signal, because
            # every glance at it costs a decision. It stays in the Strategy
            # Ledger, which is where negative results belong.
            st.markdown(f"**Underpriced — cheap, with a real role** "
                        f"({M['cheap_top']['points']:+.2f} DK points above the price-implied line)")
            st.dataframe(_frame(mis["targets"], limit=15), width="stretch", hide_index=True)

            st.caption(
                f"\"vs price\" is expected DK points above or below what the salary implies, "
                f"not a projection — add it to a projection, never read it as one. "
                f"Cheap means at or below the position's median salary on this slate. "
                f"Lists are the top and bottom fifth **within each position**: "
                f"quarterbacks all sit at 100% snap share, so ranking across positions "
                f"filled the whole target list with them. "
                f"Supported at {', '.join(mis['target_support'])}; a player from any other "
                f"position is flagged unproven rather than dropped."
            )

            with st.expander("What was tested, including what failed"):
                st.markdown(f"""
**DraftKings' own price movement predicts nothing.** The idea was that DK lags a
role change, so a stale price is a buying signal. Both the dollar change and the
percentage change span zero ({M['price_movement_sd']:+.3f} SD, CI
{M['price_movement_ci'][0]:.3f} to {M['price_movement_ci'][1]:.3f}), and adding them to
the model does not improve it out of sample. Recorded because it looks obviously
right and would otherwise be tried again.

**Last game's snap share and touches are the whole signal**, and they beat this
app's own projection at the job. Combining the two is *worse* than usage alone
({M['cheap_top']['sd']:+.3f} SD against {0.129:+.3f}), so the board deliberately
ignores the projection.

**Only cheap players.** Held out over {M['slates']} slates, top/bottom fifth within position:

| population | effect (SD) | 95% CI | points |
|---|---|---|---|
| cheap half, top fifth | {M['cheap_top']['sd']:+.3f} | {M['cheap_top']['ci'][0]:.3f} to {M['cheap_top']['ci'][1]:.3f} | {M['cheap_top']['points']:+.2f} |
| cheap half, bottom fifth | {M['cheap_bottom']['sd']:+.3f} | {M['cheap_bottom']['ci'][0]:.3f} to {M['cheap_bottom']['ci'][1]:.3f} | {M['cheap_bottom']['points']:+.2f} |
| dear half, top fifth | {M['dear_top']['sd']:+.3f} | {M['dear_top']['ci'][0]:.3f} to {M['dear_top']['ci'][1]:.3f} | spans zero |
| dear half, bottom fifth | {M['dear_bottom']['sd']:+.3f} | {M['dear_bottom']['ci'][0]:.3f} to {M['dear_bottom']['ci'][1]:.3f} | {M['dear_bottom']['points']:+.2f} |

Per position this holds at QB, RB and WR and spans zero at TE. Do not add the
per-position figures up — they are one measurement on subsets.

The fade half of this measurement is not shown on the board. It was tested over
three weeks and failed: 2 of 84 flagged players reached 20+ points, a 2% hit
rate against an 8% base rate, and the biggest disagreements did worst. A refuted
signal on a working screen costs a decision every time it is glanced at. It
lives in the Strategy Ledger, which is where negative results belong.

One SD is roughly {M['sd_to_points']:.1f} DK points. This is small, it is measured against
price rather than against a field, and it does not make a lineup safe or a profit
expected.
""")
            if mis["unranked"]:
                with st.expander(f"Not ranked — {len(mis['unranked'])} players, and why"):
                    reasons = {}
                    for r in mis["unranked"]:
                        reasons[r["why"]] = reasons.get(r["why"], 0) + 1
                    st.dataframe(pd.DataFrame(
                        [{"Reason": k, "Players": v} for k, v in
                         sorted(reasons.items(), key=lambda kv: -kv[1])]),
                        width="stretch", hide_index=True)
                    st.caption("Listed rather than dropped. A player absent from the weekly "
                               "stats release had no stat line, which is usually a blocking "
                               "tight end and genuinely zero touches — but it is "
                               "indistinguishable from a failed join, and zero touches at a "
                               "high snap share would score as strongly overpriced.")

        # ---- Injury wire, at the bottom and closed ----------------------
        # It was at the top and open. That is backwards: it is a cross-check on
        # a handful of players, not the thing the page is for, and it pushed the
        # lineups themselves below the fold. The count stays visible in the
        # expander label so a removal is never silent.
        if conflicts:
            removed = [c for c in conflicts if c["live_status"] in injuries.BLOCKING]
            doubt = [c for c in conflicts if c["live_status"] not in injuries.BLOCKING]
            with st.expander(f"⚕️ Injury wire disagrees with the salary file on "
                             f"{len(conflicts)} players — {len(removed)} removed", expanded=False):
                st.dataframe(pd.DataFrame([{
                    "Player": c["name"], "Pos": c["position"], "Team": c["team"],
                    "Salary file": c["status"], "Injury wire": c["live_status"],
                    "Body part": c["body_part"] or "—",
                    "Removed": "yes" if c["live_status"] in injuries.BLOCKING else "no",
                    "Note": c["note"][:90],
                } for c in conflicts]), width="stretch", hide_index=True)
                st.caption("The wire is live; the salary file is frozen at export. Where they "
                           "disagree the wire is usually newer — but check DraftKings before "
                           "entering, because only their ruling decides whether a lineup is legal.")
                if doubt:
                    st.caption("Players merely listed questionable are kept. A hamstring or calf "
                               "return is the exception worth acting on: those miss their price by "
                               "0.185 and 0.438 SD. Knee, ankle, shoulder, concussion and groin "
                               "returns are all priced correctly and are not faded.")

        spent = sum(e.fee for e in entries)
        c1, c2, c3 = st.columns(3)
        c1.metric("Entry fees", f"${spent:,.0f}", f"${budget - spent:,.0f} unspent"
                  if budget - spent else "budget fully used")
        c2.metric("Lineups", f"{len(lineups)}", f"{sum(map(is_stacked, lineups))} stacked")
        c3.metric("Contests", f"{len({e.contest['id'] for e in entries})}")
        for n in entry_notes:
            st.caption(f"⚠️ {n}")

        st.markdown("#### Getting these into DraftKings")
        e1, e2 = st.columns(2)
        e1.download_button("Download as a plain list", to_dk_csv(lineups),
                           file_name="fourth-and-value.csv", mime="text/csv",
                           help="Names and IDs, for reading or pasting into a spreadsheet. "
                                "DraftKings will not import this file.")
        with e2.popover("Fill a DraftKings upload template"):
            st.markdown(
                "DraftKings only accepts **their own** template, because it carries the Entry "
                "IDs that say which entry each row belongs to.\n\n"
                "1. Enter your contests first — one entry per lineup.\n"
                "2. On a contest's **Enter/Edit** screen choose **Export lineups** to get the "
                "template.\n"
                "3. Drop it here. Your lineups go into the nine position columns; every other "
                "column is left exactly as it was.\n"
                "4. Upload the file it gives you back.")
            up = st.file_uploader("DraftKings template", type="csv", key="dk_tpl")
            if up is not None:
                filled, notes = fill_dk_template(up.getvalue().decode("utf-8-sig"), lineups)
                for n in notes:
                    (st.success if n.startswith("Filled") else st.warning)(n)
                if filled:
                    st.download_button("Download the filled template", filled,
                                       file_name="dk-upload-filled.csv", mime="text/csv")
        st.caption("Projections here are DraftKings' AvgPointsPerGame — last season's average, "
                   "not a forecast. No matchup, role or injury information is in them.")

        st.divider()
        st.markdown("### Contests entered")
        saved = st.session_state.entered
        if not saved:
            st.caption("Tick **Entered** under a lineup once you have submitted it. Saved "
                       "lineups are kept off the board when you generate new ones, so you do "
                       "not enter the same nine players twice.")
        else:
            st.metric("Committed", f"${ent.total_fees(saved):,.2f}",
                      f"{len(saved)} lineup{'s' if len(saved) != 1 else ''}")
            for key, e in list(saved.items()):
                with st.container(border=True):
                    head, kill = st.columns([7, 1])
                    head.markdown(f"**{e['contest']}** · ${e['fee']:,.0f}")
                    head.caption(" · ".join(f'{slot} {p["name"]}'
                                            for slot, p in order_roster(e["players"])))
                    if kill.button("Remove", key=f"rm_{key}"):
                        saved.pop(key, None)
                        st.rerun()
            st.download_button("Export what you entered", ent.to_csv(saved),
                               file_name="contests-entered.csv", mime="text/csv")
            st.caption("This list lives in your browser session — it survives switching tabs "
                       "and rebuilding lineups, but not a refresh. Export it if you want to "
                       "keep it.")

with tab_pool:
    # ---- What the Kalshi prop markets imply, and what it changes: NOTHING ----
    # Asked for directly. The honest headline goes first: no salary, projection,
    # ceiling or lineup on this page is touched by any of it. The panel exists so
    # the signal can be judged before it is trusted, which is the order this
    # project works in.
    _kalshi_props = load_json(DATA / "kalshi-props.json")
    _kjoined = kalshi_mod.join_to_pool(pool, _kalshi_props, blend_mod.key)
    _khave = [r for r in _kjoined if r["kalshi_points"] is not None]
    KM = kalshi_mod.MEASURED

    st.markdown("### What the betting markets imply")
    if not _khave:
        st.caption("No Kalshi ladders matched this slate.")
    else:
        st.warning(
            f"**These numbers change nothing.** No projection, ceiling, salary or lineup on "
            f"this page uses them. Kalshi is shown so it can be judged, not applied — "
            f"the project standard is that nothing becomes a default until it shows held-out "
            f"value, and it has two weeks of evidence, not a season.", icon="ℹ️")
        st.caption(
            f"**Kalshi totals are PARTIAL.** The ladders price yards and receptions; the "
            f"touchdown markets returned no players, and a touchdown is 4 DK points for a pass "
            f"and 6 for a rush or catch. A Kalshi number will read low against a projection "
            f"always, and that gap is a missing term rather than a disagreement. "
            f"{len(_khave)} of {len(pool)} players have a market.")

        _kdf = pd.DataFrame([{
            "Pos": r["position"], "Player": r["name"], "Team": r["team"],
            "Salary": r["salary"],
            "Our proj": round(r["projection"], 1),
            "Kalshi (no TD)": round(r["kalshi_points"], 1),
            "Gap": round(r["projection"] - r["kalshi_points"], 1),
            "Priced": ", ".join(x.replace("_", " ") for x in r["kalshi_covered"]),
            "Rungs": r["kalshi_rungs"],
        } for r in sorted(_khave, key=lambda r: -(r["projection"] - r["kalshi_points"]))])

        st.markdown("**Where our projection is highest above the market** — "
                    "the top of this list is where we are most optimistic relative to people "
                    "with money on it, though part of every gap is the missing touchdown.")
        st.dataframe(_kdf.head(15), width="stretch", hide_index=True,
                     column_config={"Salary": st.column_config.NumberColumn("Salary", format="$%d")})
        st.markdown("**Where the market is above our projection** — "
                    "these are the ones worth a second look, because the missing touchdown "
                    "works AGAINST the market here and it is still higher.")
        st.dataframe(_kdf.tail(10).iloc[::-1], width="stretch", hide_index=True,
                     column_config={"Salary": st.column_config.NumberColumn("Salary", format="$%d")})

        # ---- WR2/3 and RB2/3 where real money disagrees with us ------------
        _watch = kalshi_mod.watchlist(pool, _kalshi_props, blend_mod.key)
        WR = kalshi_mod.WATCHLIST_RECORD
        # Collapsed, not a panel. Logged every week because that is the only way
        # the question gets answered, but it has a record now and the record is
        # bad -- so it does not get to sit on the screen looking like advice.
        _exp = st.expander(
            f"Market watchlist — logged weekly, {WR['popped']} of {WR['graded']} picks have hit "
            f"({WR['hit_rate']:.0%} against a {WR['base_rate']:.0%} base rate)")
        with _exp:
            st.caption(
                f"WR2/WR3 and RB2/RB3 where the market has traded money and sits above our "
                f"projection. Three weeks in it is worse than picking at random, and the biggest "
                f"disagreements did worst — {WR['by_gap']['over 3 pts'][0]} of "
                f"{WR['by_gap']['over 3 pts'][1]} where the market was 3+ points above us. "
                f"Kept because it is banked every week and the answer needs about 130 picks; "
                f"there are {WR['graded']}.")
        if _watch:
          with _exp:
            st.dataframe(pd.DataFrame([{
                "Slot": r["depth"], "Player": r["name"], "Team": r["team"],
                "Status": _flag(r["name"]) or "—",
                "Salary": r["salary"], "We say": r["ours_noTD"], "Kalshi": r["kalshi"],
                "Gap": r["gap"], "Traded": r["volume"],
            } for r in _watch[:15]]), width="stretch", hide_index=True,
                column_config={"Salary": st.column_config.NumberColumn("Salary", format="$%d"),
                               "Traded": st.column_config.NumberColumn("Traded", format="%d")})
            st.caption(
                f"Second or third on his own team by salary, market traded at least "
                f"{kalshi_mod.MIN_VOLUME:.0f}, and the market priced what he actually does. "
                f"{len(_watch)} players pass. Both numbers exclude touchdowns.")

        with st.expander("Is it any good? The first forward validation"):
            st.markdown(f"""
This could not be tested for most of the project's life: settled Kalshi markets
have empty books, so a week not captured before kickoff is gone for good. The
scheduled snapshot job has been building the archive, and there are now pre-lock
captures for **Week 1 and Week 2** with graded results for both.

Scored on the **{KM['target']}** across **{KM['players']} player-weeks**:

| estimator | MAE | correlation |
|---|---|---|
| **Kalshi implied** | **{KM['kalshi_mae']:.2f}** | **{KM['kalshi_corr']:.3f}** |
| DraftKings' own average, rescaled | {KM['dk_mae']:.2f} | {KM['dk_corr']:.3f} |

{KM['note']}

**Two weeks is not a measurement.** It is the first two data points of one, and
the advantage is driven by Week 2 — Week 1 was a dead heat. For scale, the
prior-season blend that does ship was worth 0.541 MAE and was measured over 40
slates. This is 0.46 over two. Reproduce with
`app/scripts/test_kalshi_projection.py`.
""")
    st.divider()

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
        "Context": context.summary(p),
        "Net (SD)": context.net_sd(p),
        "Ceiling from": p["ceiling_source"],
    } for p in pool]).sort_values(["Lineups", "Proj"], ascending=False)
    c1, c2 = st.columns([1, 3])
    pos = c1.multiselect("Position", ["QB", "RB", "WR", "TE", "DST"])
    if pos:
        df = df[df["Pos"].isin(pos)]
    c2.caption(f"{len(df)} players. Backup quarterbacks and anyone Out, Doubtful or on "
               f"injured reserve are already removed.")
    st.dataframe(df, width="stretch", hide_index=True, height=560)
    st.caption("**Context** lists only what this build can actually establish, with the "
               "measured effect in standard deviations of the salary residual — one SD is "
               "about 6.9 DK points. These are shown, not applied: measured at the lineup "
               "level, home and dome together are worth +1.55 points, against ~3.6 if they "
               "simply added across a roster. They do not add up.")
    with st.expander("What this build cannot see, and what it would be worth"):
        for label, why in context.NOT_AVAILABLE:
            st.markdown(f"**{label}** — {why}")

with tab_entry:
    # ---- Marked entered on the Lineups tab, this session ---------------------
    # Separate from the file-backed block below: that is a transcription of the
    # DraftKings entry screen, this is what you ticked here. Both are "money
    # staked" and neither is the plan, but only one of them survives a reload,
    # so they are never merged into one table.
    if st.session_state.entered:
        st.markdown("### Marked entered this session")
        rows = []
        for rec in st.session_state.entered.values():
            roster = rec["players"]
            qb = next((r for r in roster if r.get("position") == "QB"), {})
            rows.append({
                "Contest": rec["contest"],
                "Fee": rec["fee"],
                "QB": f'{qb.get("name", "—")} ({qb.get("team", "")})',
                "Salary used": sum(r.get("salary", 0) for r in roster),
                "Players": ", ".join(r["name"] for r in roster),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                     column_config={
                         "Fee": st.column_config.NumberColumn("Fee", format="$%d"),
                         "Salary used": st.column_config.NumberColumn("Salary used", format="$%d"),
                     })
        staked = ent.total_fees(st.session_state.entered)
        left = budget - staked
        st.caption(
            f"**${staked:,.0f} staked across {len(st.session_state.entered)} "
            f"{'entry' if len(st.session_state.entered) == 1 else 'entries'}** — "
            + (f"${left:,.0f} of your ${budget:,.0f} budget left."
               if left >= 0 else
               f"**${-left:,.0f} over** your ${budget:,.0f} budget.")
            + " These lineups are removed from the Lineups tab so the same nine"
              " players are not offered to you twice. This list is held in the"
              " browser session and does NOT survive a reload — export it, or"
              " re-tick after refreshing.")
        # Undo lives HERE, not on the lineup card. Marking a lineup entered
        # removes it from the board on the next build, so the card carrying an
        # "undo" control is exactly the card that no longer renders -- the first
        # version put it there and it was unreachable the moment it was needed.
        undo_col, dl_col = st.columns([3, 2])
        keys = list(st.session_state.entered)
        labels = []
        for k in keys:
            rec = st.session_state.entered[k]
            qb_name = next((r["name"] for r in rec["players"]
                            if r.get("position") == "QB"), "—")
            labels.append(f'{rec["contest"]} · {qb_name}')
        which = undo_col.selectbox("Unmark an entry", range(len(keys)),
                                   format_func=lambda n: labels[n], key="unmark_pick")
        if undo_col.button("Unmark — put this lineup back on the board"):
            st.session_state.entered.pop(keys[which], None)
            st.rerun()
        dl_col.download_button("Download these entries (CSV)",
                               ent.to_csv(st.session_state.entered),
                               file_name="entries-marked.csv", mime="text/csv")
        st.divider()

    # ---- What was ACTUALLY entered ------------------------------------------
    # This goes first, above the plan. The plan below is a recommendation; this
    # is money already staked, and the two must not be confused. Hidden entirely
    # when the file is absent, which is the case on the public deploy.
    placed = load_placed_entries(_data_version())
    if placed["entries"]:
        st.markdown("### Contests you have entered")
        p_rows = []
        for e in placed["entries"]:
            c = ent.placed_contest(placed, e.get("contestId", ""))
            roster = e.get("roster", [])
            qb = next((r for r in roster if r.get("slot") == "QB"), {})
            idx = ent.match_generated(roster, lineups)
            best = max((ent.overlap_with(roster, l) for l in lineups), default=0)
            p_rows.append({
                "#": e.get("entry"),
                "Contest": c.get("name", "—"),
                "Fee": c.get("entryFee", 0.0),
                "Prizes": c.get("totalPrizes", 0),
                "Rake %": rake_pct(c) if c else 0.0,
                "QB": f'{qb.get("name", "—")} ({qb.get("team", "")})',
                "Left": 50000 - sum(r.get("salary", 0) for r in roster),
                "On the board": (f"= lineup {idx + 1}" if idx is not None
                                 else f"no — closest shares {best}/9"),
            })
        p_table = pd.DataFrame(p_rows)
        st.dataframe(
            p_table, width="stretch", hide_index=True,
            column_config={
                "Fee": st.column_config.NumberColumn("Fee", format="$%d"),
                "Prizes": st.column_config.NumberColumn("Prizes", format="$%d"),
                "Rake %": st.column_config.NumberColumn("Rake %", format="%.1f%%"),
                "Left": st.column_config.NumberColumn("Cap left", format="$%d"),
            })

        staked = ent.placed_fees(placed)
        contests_n = len({e.get("contestId") for e in placed["entries"]})
        rakes = [r for r in p_table["Rake %"] if r]
        k1, k2, k3 = st.columns(3)
        k1.metric("Staked", f"${staked:,.0f}", f"{len(placed['entries'])} entries")
        k2.metric("Contests", f"{contests_n}")
        k3.metric("Rake on that money", f"{(sum(rakes) / len(rakes) if rakes else 0):.1f}%",
                  "worst band on the board", delta_color="inverse")

        with st.expander("The nine players in each"):
            for e in placed["entries"]:
                c = ent.placed_contest(placed, e.get("contestId", ""))
                st.markdown(f'**#{e.get("entry")} · {c.get("name", "—")}** · '
                            f'${c.get("entryFee", 0):.0f}')
                # The recorded slot comes from DraftKings' own entry screen, so
                # it is authoritative -- do NOT re-derive it with order_roster,
                # which needs a projection these rows do not carry and would be
                # guessing at something already known.
                st.dataframe(
                    pd.DataFrame([{"Slot": pl.get("slot", "?"), "Player": pl["name"],
                                   "Team": pl.get("team", ""), "Salary": pl.get("salary", 0)}
                                  for pl in e.get("roster", [])]),
                    width="stretch", hide_index=True,
                    column_config={"Salary": st.column_config.NumberColumn(
                        "Salary", format="$%d")})

        st.caption(
            f"Transcribed from the DraftKings entry screen on {placed.get('transcribed', '—')} "
            "and checked against the salary file — every lineup reconciles to the cap and "
            "every player resolves to exactly one person on the slate. These are placed "
            "bets, not suggestions. The plan below is the suggestion.")
        st.divider()

    # ---- Open board, with the overlay that is actually on it right now ------
    # A guaranteed contest pays its full prize pool whether it fills or not, so
    # one still short of capacity is paying out more than the entrants put in.
    # That is the only thing on this page that is free money rather than a
    # smaller loss -- and it is also the most perishable, which is why the
    # snapshot time is stated everywhere it appears.
    board = load_open_board(_data_version())
    if board and board.get("contests"):
        staked_now = ent.placed_fees(placed)
        left = max(0.0, budget - staked_now)
        st.markdown("### Still open, and what is left to spend")
        st.caption(
            f"Board captured **{board.get('captured', 'unknown')}**. Contests fill and close; "
            f"anything here can be gone by lock, and the fill percentages are from that capture "
            f"rather than live.")
        h1, h2, h3 = st.columns(3)
        h1.metric("Budget", f"${budget:,.0f}")
        h2.metric("Already staked", f"${staked_now:,.0f}",
                  f"{len(placed['entries'])} entries", delta_color="off")
        h3.metric("Left to spend", f"${left:,.0f}")
        max_fee = st.select_slider(
            "Show contests up to", options=[3, 5, 10, 25, 50, 100, 1000, 5000],
            value=min([o for o in [3, 5, 10, 25, 50, 100, 1000, 5000] if o >= left] or [5000]),
            format_func=lambda v: f"${v}")
        b = []
        for c in board["contests"]:
            if c["entryFee"] > max_fee:
                continue
            cap = c["maxEntries"] * c["entryFee"]
            snap = c["entered"] * c["entryFee"]
            b.append({
                "Contest": c["name"], "Fee": c["entryFee"], "Prizes": c["totalPrizes"],
                "Full": c["entered"] / c["maxEntries"] * 100,
                "Rake if it fills": (cap - c["totalPrizes"]) / cap * 100,
                "Rake right now": (snap - c["totalPrizes"]) / snap * 100 if snap else 0.0,
                "Overlay": max(0, c["totalPrizes"] - snap),
                "Fits": "yes" if c["entryFee"] <= left else "over budget",
            })
        # Overlay first, because it is the only thing on this board that is
        # money the field has not put in. Ties broken by the cheaper entry.
        b.sort(key=lambda r: (-r["Overlay"], r["Fee"]))
        st.dataframe(
            pd.DataFrame(b), width="stretch", hide_index=True,
            column_config={
                "Fee": st.column_config.NumberColumn("Fee", format="$%d"),
                "Prizes": st.column_config.NumberColumn("Prizes", format="$%d"),
                "Full": st.column_config.NumberColumn("Full", format="%.0f%%"),
                "Rake if it fills": st.column_config.NumberColumn("Rake if full", format="%.1f%%"),
                "Rake right now": st.column_config.NumberColumn("Rake now", format="%.1f%%"),
                "Overlay": st.column_config.NumberColumn("Overlay", format="$%d"),
            })
        afford = [r for r in b if r["Fee"] <= left and r["Overlay"] > 0]
        if afford and left > 0:
            top = afford[0]
            # Contest names carry their own dollar signs ("$50K to 1st"), which
            # Streamlit reads as LaTeX exactly like the ones in the sentence.
            # Escaping only the sentence's dollars left the NAME rendering as
            # maths, which is how this was caught.
            esc = lambda t: str(t).replace("$", "\\$")
            st.success(
                f"**Most overlay you can still reach with \\${left:,.0f}:** {esc(top['Contest'])} — "
                f"\\${top['Fee']:.0f} to enter, {top['Full']:.0f}% full, "
                f"\\${top['Overlay']:,.0f} of the prize pool not yet paid for by entrants. "
                f"At capacity it would keep {top['Rake if it fills']:.1f}%.")
        _lock = next((c.get("lockTime") for c in board["contests"] if c.get("lockTime")), "lock")
        st.caption(
            f"Board captured {str(board.get('captured', ''))[:16].replace('T', ' ')}, locking "
            f"{_lock}. **Entry counts move hard in the last hours** — on the Week 3 board they "
            "roughly doubled between Friday morning and Friday night, and the $15K Hail Mary "
            "filled and closed in that window. An overlay shown here is what was true at "
            "capture, not a forecast of lock. "
            "A negative rake means the house is currently topping up the prize pool. "
            "It makes the hole shallower; it is not an edge, and this tool has never "
            "demonstrated one.")
        st.divider()

    if _board_stale:
        st.error(
            f"**This contest board is from {_captured('contests')} and the slate is from "
            f"{_captured('slate')}.** The tournaments below are last week's and cannot be "
            "entered. Transcribe the current lobby into `data/lobby-week<N>-2026.json` before "
            "using this plan.", icon="⚠️")
    # ---- Scored on what was measured, not on rake alone ---------------------
    # ---- One plan, not two lists of the same contests --------------------
    # This tab used to render "Best contests for your budget" and "The plan"
    # back to back. Both came from the same scorer, so the same contests were
    # listed twice and the page read as though it were recommending twice as
    # many as the budget could buy.
    st.markdown(f"### Your ${budget:,.0f}")
    if not entries:
        st.warning("No contest on this slate fits the budget with a known payout shape.")
    else:
        _spent = sum(e.fee for e in entries)
        _rake = sum(e.fee * (rake_pct(e.contest) or 0) / 100 for e in entries)
        c1, c2, c3 = st.columns(3)
        c1.metric("Staked", f"${_spent:,.0f}", f"of ${budget:,.0f}", delta_color="off")
        c2.metric("Entries", f"{len(entries)}", f"{len(lineups)} lineups built", delta_color="off")
        c3.metric("Blended rake", f"{100*_rake/_spent:.1f}%", f"${_rake:,.0f} to the house",
                  delta_color="off")
        _table = pd.DataFrame([{
            "Entered": f"{i}|{e.contest['id']}" in st.session_state.contests_done,
            "Lineup": i + 1,
            "Contest": e.contest["name"],
            "Fee": e.fee,
            "Rake": rake_pct(e.contest),
            "Field": e.contest.get("maxEntries"),
            "Full": (100 * e.contest.get("entered", 0) / e.contest["maxEntries"]
                     if e.contest.get("maxEntries") else None),
            "Limit": e.contest.get("maxEntriesPerUser"),
            "Why this one": e.reason,
        } for i, e in enumerate(entries)])
        _edited = st.data_editor(
            _table, width="stretch", hide_index=True, key="entry_plan_editor",
            disabled=[c for c in _table.columns if c != "Entered"],
            column_config={
                "Entered": st.column_config.CheckboxColumn("Entered", width="small"),
                "Fee": st.column_config.NumberColumn("Fee", format="$%d"),
                "Rake": st.column_config.NumberColumn("Rake", format="%.1f%%"),
                "Field": st.column_config.NumberColumn("Field", format="%d"),
                "Full": st.column_config.NumberColumn("Full", format="%.0f%%"),
            })
        st.session_state.contests_done = {
            f"{i}|{entries[i].contest['id']}"
            for i, flag in enumerate(_edited["Entered"]) if flag}
        _done = _edited[_edited["Entered"]]
        if len(_done):
            st.caption(f"**${_done['Fee'].sum():,.0f} entered** across {len(_done)} of "
                       f"{len(entries)} contests · ${_spent - _done['Fee'].sum():,.0f} still to go.")
        if len(entries) < len(lineups):
            st.caption(
                f"**{len(entries)} contests for {len(lineups)} lineups.** The budget does not "
                f"stretch to ten good seats; it buys {len(entries)}. Build fewer lineups, or "
                f"raise the budget and the table above will grow.")
    for n in entry_notes:
        st.warning(n)

    # ---- What just missed, and why ---------------------------------------
    _ranked = select_mod.ranked(lobby["contests"])
    _taken = {e.contest["id"] for e in entries}
    _next = [s for s in _ranked if s.contest["id"] not in _taken][:6]
    if _next:
        with st.expander(f"Also worth playing if you raise the budget — {len(_next)} more"):
            st.dataframe(
                pd.DataFrame([{
                    "Contest": s.contest["name"], "Fee": s.contest["entryFee"],
                    "Rake": rake_pct(s.contest),
                    "Field": s.contest.get("maxEntries"),
                    "Why": "; ".join(s.reasons),
                } for s in _next]),
                width="stretch", hide_index=True,
                column_config={
                    "Fee": st.column_config.NumberColumn("Fee", format="$%d"),
                    "Rake": st.column_config.NumberColumn("Rake", format="%.1f%%"),
                    "Field": st.column_config.NumberColumn("Field", format="%d"),
                })
            st.caption("Ranked the same way as the table above. These were cut by the budget, "
                       "not by merit — the cheapest of them is "
                       f"${min(s.contest['entryFee'] for s in _next):,.0f}.")
    st.caption(
        "Scored on rake, top-prize size, single-entry and overlay — all measured here. A contest "
        "whose payout curve has not been seen is refused rather than assumed: on the Week 2 board "
        "a double-up and a lottery both showed 10.0% rake, the best number on it. **None of this "
        "is an edge.** Every contest keeps 12–15%, and a player with no edge loses that.")
    st.divider()

    st.caption(
        "Rake is what DraftKings keeps, measured against a full field. Buying up is the one "
        "contest lever measured here that costs nothing — it makes the hole shallower, it does "
        "not make it a profit.")
    st.caption(f"Contest data hand-transcribed from lobby screenshots ({lobby['captured']}). "
               "Prize pools and fees are stable; entry counts move.")

with tab_strategy:
    S = load_strategy(_data_version())
    st.markdown("### Every idea this project has tested")
    st.caption("This exists because the expensive mistake is re-implementing something already "
               "measured and rejected. Nothing is deleted when it fails — a removed row cannot "
               "stop the idea being tried again.")

    counts = {}
    for r in S["ledger"]:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    cols = st.columns(len(S["verdictOrder"]))
    for col, v in zip(cols, S["verdictOrder"]):
        col.metric(S["verdictLabel"][v], counts.get(v, 0), help=S["verdictMeaning"][v])

    for v in S["verdictOrder"]:
        rows = sorted([r for r in S["ledger"] if r["verdict"] == v],
                      key=lambda r: r["claim"].lower())
        if not rows:
            continue
        with st.expander(f"{S['verdictLabel'][v]} — {len(rows)}", expanded=(v == "adopted")):
            st.caption(S["verdictMeaning"][v])
            for r in rows:
                st.markdown(f"**{r['claim']}**")
                st.markdown(r["plain"])
                if r.get("result"):
                    st.caption(f"{r['result']} · {r['sample']}")
                st.divider()

    st.markdown("### Claims from the strategy articles")
    st.caption(f"{len(S['claims'])} individual claims pulled out of published DFS writing and "
               f"measured over {S['slatesMeasured']} slates. The effect is how much better or "
               f"worse a player does than his DraftKings price implies — in DK points, so "
               f"+1.5 means about a point and a half above what you paid for.")
    st.warning("**Do not add these up.** The same effect appears in several rows measured on "
               "different groups of players, and no nine players can be at home, indoors, "
               "high-volume and in good matchups at once. Home and dome together are worth "
               "+1.55 points to a lineup, against the ~3.6 you would get by adding them up.")

    # Plain-language section headings. "Unsupported" is split into the two ways
    # a claim can fail, because "we measured the opposite" and "we could not
    # tell either way" are different answers and collapsing them loses that.
    SECTIONS = [
        ("supported", "Supported — the measurement backs the claim", True),
        ("reversed", "Unsupported — measured, and it went the other way", False),
        ("null", "Unsupported — measured, no effect found", False),
        ("untested", "Not yet tested", False),
        ("blocked", "Cannot be tested with the data we have", False),
    ]

    def claim_table(rows):
        return pd.DataFrame([{
            "Claim": c["claim"],
            "Where it came from": sources.sites(c.get("articles", "")),
            "Effect (DK points)": (round(c["effect"] * S["pointsPerSd"], 2)
                                   if c.get("effect") is not None else None),
            "Note": c.get("note", ""),
        } for c in rows])

    for status, heading, default_open in SECTIONS:
        rows = sorted([c for c in S["claims"] if c["status"] == status],
                      key=lambda c: c["claim"].lower())
        if not rows:
            continue
        with st.expander(f"{heading} — {len(rows)}", expanded=default_open):
            st.caption(S["statusMeaning"][status])
            st.dataframe(claim_table(rows), width="stretch", hide_index=True,
                         height=min(600, 80 + 35 * len(rows)))

with tab_about:
    st.markdown(f"""
### Where the numbers come from

| Source | What it provides | Freshness |
|---|---|---|
| **DraftKings salary export** | The slate: players, salaries, positions, kickoffs, and the status flag used to drop anyone Out, Doubtful or on injured reserve | Captured {_captured("slate")} |
| **DraftKings lobby** | 109 tournaments with fees, prize pools, entry caps and lock times, hand-transcribed from screenshots | Captured {_captured("contests")} |
| **Historical box scores, 2000–2025** | Each player's own scoring spread, which is where the upside number comes from. 367 of 481 players matched; the rest are labelled | Through 2025 |
| **nflverse** | Defensive scoring history, used to rebuild defence results that the box-score data does not carry | Through 2025 |
| **Published DFS strategy writing** | 91 individual claims, broken out and measured. The Strategy tab is the result | 12 articles + one strategy hub |

**Projections are DraftKings' own `AvgPointsPerGame`** — last season's average.
Not a forecast. Nothing in it knows about the matchup, a changed role, a new
offensive coordinator, or an injury.

**There is no live feed of anything in this build.** No injury wire, no weather,
no betting lines. Player availability is whatever the salary file said on the
day it was exported. Check DraftKings before entering.

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

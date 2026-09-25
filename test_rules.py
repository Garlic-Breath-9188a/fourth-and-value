"""
Tests for the rules that came from real bugs.

Each test names the bug it prevents. They are not coverage; they are the
specific things that were wrong on screen at some point and were fixed.
Run with:  python3 -m unittest test_rules -v
"""
import csv, io, json, unittest
from datetime import datetime, timedelta
from pathlib import Path

from fv import rules
from fv import pool as pool_mod
from fv import injuries
from fv import entered as ent
from fv import blend as blend_mod
from fv.slate import parse_kickoff, kickoff_windows, slate_options, main_slate, restrict, in_slate
from fv.pool import load_salaries, keep_starting_quarterbacks, apply_ceilings, rosterable, load_json
from fv.roster import order_roster, stack_role, is_stacked, fill_dk_template, DK_SLOTS
from fv.optimize import build_portfolio, build_lineup
from fv.entry import plan, rake_pct
from fv import entry as entry_mod
from fv import shape
from fv import select
import random

DATA = Path(__file__).resolve().parent / "data"


def live_pool():
    rows = load_salaries((DATA / "DKSalaries.csv").read_text())
    rows = [r for r in keep_starting_quarterbacks(rows) if rosterable(r)]
    rows = apply_ceilings(rows, load_json(DATA / "player-variance.json"))
    slate = main_slate(slate_options(rows))
    kept, _ = restrict(rows, slate)
    return kept, slate


class Slate(unittest.TestCase):
    """Bug: lineup #1 contained Kansas City players whose game is Monday night."""

    def test_windows_split_on_a_gap(self):
        times = [parse_kickoff(f"X {d}") for d in
                 ["09/09/2026 08:20PM ET", "09/13/2026 01:00PM ET",
                  "09/13/2026 04:25PM ET", "09/13/2026 08:20PM ET", "09/14/2026 08:15PM ET"]]
        self.assertEqual([len(w) for w in kickoff_windows(times)], [1, 2, 1, 1])

    def test_main_slate_excludes_night_games(self):
        # Asserts the SHAPE of the main slate, not a specific date. The first
        # version hard-coded Week 1's 2026-09-13 and broke the moment the Week 2
        # salary file shipped -- a correct change failing the suite.
        _, slate = live_pool()
        self.assertEqual(slate.locks_at.weekday(), 6, "the main slate is a Sunday")
        self.assertEqual(slate.locks_at.hour, 13, "and it locks at 1:00pm")
        self.assertGreaterEqual(slate.game_count, 8, "a main slate is most of the week")
        self.assertLess(slate.ends_at.hour, 20, "and it ends before the night game")

    def test_slate_is_bounded_at_the_top(self):
        """The original bug: no end bound, so Monday night was 'after the lock'."""
        _, slate = live_pool()
        # A Monday night kickoff, relative to whatever Sunday this slate is.
        monday = slate.locks_at + timedelta(days=1, hours=7)
        stamp = monday.strftime("DEN@KC %m/%d/%Y %I:%M%p ET")
        self.assertFalse(in_slate(stamp, slate.locks_at, slate.ends_at))

    def test_unparseable_kickoff_is_kept(self):
        self.assertTrue(in_slate("no date here", datetime(2026, 9, 13, 13, 0)))


class Roster(unittest.TestCase):
    """Bug: 7 of 10 lineups showed two tight ends, and slots were mislabelled."""

    def test_no_tight_end_in_the_flex(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 10, seed=3, attempts=6000)
        self.assertTrue(lineups)
        for l in lineups:
            self.assertLessEqual(sum(1 for p in l if p["position"] == "TE"), 1)

    def test_dk_slot_order(self):
        pool, _ = live_pool()
        l = build_portfolio(pool, 1, seed=5, attempts=4000)[0]
        self.assertEqual([s for s, _ in order_roster(l)], list(DK_SLOTS))

    def test_flex_takes_the_lowest_projection(self):
        mk = lambda n, pos, proj: {"id": n, "name": n, "position": pos, "projection": proj,
                                   "salary": 5000, "team": "A", "opponent": "B", "game": "A@B"}
        roster = [mk("q", "QB", 20), mk("r1", "RB", 15), mk("r2", "RB", 12), mk("r3", "RB", 4),
                  mk("w1", "WR", 14), mk("w2", "WR", 13), mk("w3", "WR", 11),
                  mk("t", "TE", 9), mk("d", "DST", 7)]
        slots = dict((s, p["id"]) for s, p in order_roster(roster))
        self.assertEqual(slots["FLEX"], "r3")


class Stacking(unittest.TestCase):
    """The one construction rule that measured a real gain: P(180+) 0.13% -> 0.25%."""

    def test_every_lineup_is_stacked_with_a_bring_back(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 10, seed=7, attempts=8000)
        self.assertEqual(len(lineups), 10)
        for l in lineups:
            self.assertTrue(is_stacked(l), "cap-filling must not undo the stack")

    def test_roles_are_labelled(self):
        pool, _ = live_pool()
        l = build_portfolio(pool, 1, seed=9, attempts=4000)[0]
        roles = [stack_role(p, l) for p in l]
        self.assertEqual(roles.count("QB"), 1)
        self.assertGreaterEqual(roles.count("STACK"), rules.STACK_MATES)
        self.assertGreaterEqual(roles.count("BRING-BACK"), rules.STACK_BRING_BACK)


class Legality(unittest.TestCase):
    def test_dk_roster_rules(self):
        pool, _ = live_pool()
        for l in build_portfolio(pool, 10, seed=11, attempts=8000):
            self.assertEqual(len(l), 9)
            self.assertEqual(len({p["id"] for p in l}), 9)
            self.assertLessEqual(sum(p["salary"] for p in l), rules.SALARY_CAP)
            self.assertGreaterEqual(len({p["game"] for p in l}), rules.MIN_DISTINCT_GAMES)
            for team in {p["team"] for p in l}:
                self.assertLessEqual(sum(1 for p in l if p["team"] == team),
                                     rules.MAX_PLAYERS_PER_TEAM)
            counts = {}
            for p in l:
                counts[p["position"]] = counts.get(p["position"], 0) + 1
            self.assertEqual(counts.get("QB"), 1)
            self.assertEqual(counts.get("DST"), 1)
            self.assertGreaterEqual(counts.get("RB", 0), 2)
            self.assertGreaterEqual(counts.get("WR", 0), 3)
            self.assertGreaterEqual(counts.get("TE", 0), 1)

    def test_lineups_are_distinct(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 10, seed=13, attempts=8000)
        keys = {tuple(sorted(p["id"] for p in l)) for l in lineups}
        self.assertEqual(len(keys), len(lineups))
        for i, a in enumerate(lineups):
            for b in lineups[i + 1:]:
                shared = len({p["id"] for p in a} & {p["id"] for p in b})
                self.assertLessEqual(shared, rules.MAX_SHARED_PLAYERS)


class Pool(unittest.TestCase):
    """Bugs: injured-reserve players were selectable; backup QBs won on value."""

    def test_injured_reserve_is_excluded(self):
        self.assertFalse(rosterable({"status": "IR"}))
        self.assertFalse(rosterable({"status": "Out"}))
        self.assertTrue(rosterable({"status": "Questionable"}))
        self.assertFalse(rosterable({"status": "Questionable"}, exclude_questionable=True))

    def test_one_quarterback_per_team(self):
        rows = keep_starting_quarterbacks(load_salaries((DATA / "DKSalaries.csv").read_text()))
        teams = [r["team"] for r in rows if r["position"] == "QB"]
        self.assertEqual(len(teams), len(set(teams)))

    def test_estimated_ceilings_are_labelled(self):
        pool, _ = live_pool()
        for p in pool:
            self.assertTrue(p["ceiling_source"])
            if "no scoring history" not in p["ceiling_source"]:
                self.assertIn("own 80th percentile", p["ceiling_source"])


class MustPlay(unittest.TestCase):
    """
    Bug: a must-play quarterback with the exposure cap at 10% appeared in ALL
    ten lineups. "Include somewhere" had been implemented as "include always",
    and must-plays were additionally exempted from the exposure cap, so the
    control that should have stopped it was switched off too.
    """

    def _qb(self, pool, _unused=None):
        """
        A quarterback from THIS slate, picked by price rather than by name.

        This used to look up "Hurts", which fails the moment Philadelphia is not
        on the slate -- it was broken by the Week 3 export. A test that names a
        player is pinned to one week and fails for a reason that has nothing to
        do with what it is testing. Pick the most expensive quarterback: there is
        always exactly one, whatever the week.
        """
        return max((p for p in pool if p["position"] == "QB"), key=lambda p: p["salary"])

    def test_must_play_qb_appears_once_at_a_tight_cap(self):
        pool, _ = live_pool()
        qb = self._qb(pool)
        lineups = build_portfolio(pool, 10, seed=1, attempts=8000,
                                  qb_exposure=10, must_play={qb["id"]})
        holding = [l for l in lineups if any(p["id"] == qb["id"] for p in l)]
        self.assertEqual(len(holding), 1, "must-play means somewhere, not everywhere")

    def test_exposure_cap_still_binds_with_a_must_play(self):
        pool, _ = live_pool()
        qb = self._qb(pool)
        lineups = build_portfolio(pool, 10, seed=1, attempts=8000,
                                  qb_exposure=10, must_play={qb["id"]})
        starters = [next(p for p in l if p["position"] == "QB")["id"] for l in lineups]
        self.assertEqual(len(set(starters)), len(starters),
                         "a 10% cap over 10 lineups means ten different quarterbacks")

    def test_must_play_non_qb_is_placed_and_lineups_stay_legal(self):
        pool, _ = live_pool()
        wr = max((p for p in pool if p["position"] == "WR"), key=lambda p: p["salary"])
        lineups = build_portfolio(pool, 10, seed=2, attempts=8000, must_play={wr["id"]})
        self.assertTrue(any(any(p["id"] == wr["id"] for p in l) for l in lineups))
        for l in lineups:
            self.assertLessEqual(sum(p["salary"] for p in l), rules.SALARY_CAP)
            self.assertTrue(is_stacked(l), "placing a must-play must not break the stack")

    def test_several_must_plays_all_land(self):
        pool, _ = live_pool()
        picks = {self._qb(pool)["id"]}
        for pos in ("RB", "TE"):
            picks.add(max((p for p in pool if p["position"] == pos),
                          key=lambda p: p["projection"])["id"])
        lineups = build_portfolio(pool, 10, seed=4, attempts=8000, must_play=picks)
        placed = {p["id"] for l in lineups for p in l}
        self.assertEqual(picks - placed, set(), "every must-play should be placed")
        self.assertEqual(len({tuple(sorted(p["id"] for p in l)) for l in lineups}), 10)


class Wr1Stack(unittest.TestCase):
    """The QB's WR1 should be in the stack when that rule is on."""

    def test_wr1_is_always_stacked_when_required(self):
        pool, _ = live_pool()
        from fv.optimize import top_receiver
        lineups = build_portfolio(pool, 10, seed=1, attempts=8000, require_wr1=True)
        self.assertEqual(len(lineups), 10)
        for l in lineups:
            qb = next(p for p in l if p["position"] == "QB")
            wr1 = top_receiver(qb["team"], pool)
            self.assertIsNotNone(wr1)
            self.assertIn(wr1["id"], {p["id"] for p in l})
            self.assertTrue(is_stacked(l))


class DkTemplate(unittest.TestCase):
    """DraftKings only imports its own template, and only if it survives intact."""

    def _template(self, n):
        head = "Entry ID,Contest Name,Contest ID,Entry Fee,QB,RB,RB,WR,WR,WR,TE,FLEX,DST\n"
        return head + "".join(f"90{i},NFL Contest,555{i},$3,,,,,,,,,\n" for i in range(1, n + 1))

    def test_fills_ids_and_preserves_entry_columns(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 5, seed=2, attempts=6000)
        out, notes = fill_dk_template(self._template(5), lineups)
        rows = list(csv.reader(io.StringIO(out)))
        self.assertEqual(len(rows), 6)
        for i in range(1, 6):
            self.assertEqual(rows[i][0], f"90{i}", "entry id must survive untouched")
            self.assertTrue(all(c for c in rows[i][4:13]), "all nine slots filled")
        self.assertTrue(notes[0].startswith("Filled 5"))

    def test_slots_match_the_board_order(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 1, seed=3, attempts=4000)
        out, _ = fill_dk_template(self._template(1), lineups)
        row = list(csv.reader(io.StringIO(out)))[1]
        self.assertEqual(row[4:13], [str(p["id"]) for _, p in order_roster(lineups[0])])

    def test_extra_template_rows_are_left_alone(self):
        """Blanking a row would wipe an entry that may already hold a lineup."""
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 2, seed=4, attempts=6000)
        out, notes = fill_dk_template(self._template(4), lineups)
        rows = list(csv.reader(io.StringIO(out)))
        self.assertEqual(rows[3][4:13], [""] * 9)
        self.assertTrue(any("left as they were" in n for n in notes))

    def test_a_file_that_is_not_a_template_is_refused(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 1, seed=5, attempts=4000)
        out, notes = fill_dk_template("name,salary\nfoo,1\n", lineups)
        self.assertEqual(out, "")
        self.assertIn("DraftKings upload template", notes[0])


class Entered(unittest.TestCase):
    """Tracking what has been entered, and keeping it off the next board."""

    def test_identity_is_the_roster_not_the_slot(self):
        """Lineup 3 is different nine players after a seed change."""
        pool, _ = live_pool()
        a = build_portfolio(pool, 3, seed=1, attempts=6000)
        self.assertNotEqual(ent.roster_key(a[0]), ent.roster_key(a[1]))
        shuffled = list(reversed(a[0]))
        self.assertEqual(ent.roster_key(a[0]), ent.roster_key(shuffled),
                         "player order must not change a roster's identity")

    def test_record_forget_and_total(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 2, seed=2, attempts=6000)
        store = {}
        ent.record(store, lineups[0], {"name": "NFL $40K Pylon", "id": "x1"}, 3)
        ent.record(store, lineups[1], {"name": "NFL $175K Fair Catch", "id": "x2"}, 12)
        self.assertEqual(ent.total_fees(store), 15)
        self.assertTrue(ent.is_entered(store, lineups[0]))
        ent.forget(store, lineups[0])
        self.assertFalse(ent.is_entered(store, lineups[0]))
        self.assertEqual(ent.total_fees(store), 12)

    def test_export_has_a_row_per_entry_in_slot_order(self):
        pool, _ = live_pool()
        lineups = build_portfolio(pool, 1, seed=3, attempts=4000)
        store = {}
        ent.record(store, lineups[0], {"name": "NFL $40K Pylon", "id": "x1"}, 3)
        rows = list(csv.reader(io.StringIO(ent.to_csv(store))))
        self.assertEqual(rows[0][2:], list(DK_SLOTS))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "NFL $40K Pylon")

    def test_entered_rosters_are_excluded_from_a_rebuild(self):
        """The point of the feature: never offered the same nine players twice."""
        pool, _ = live_pool()
        first = build_portfolio(pool, 10, seed=4, attempts=8000)
        avoid = {ent.roster_key(first[0]), ent.roster_key(first[1])}
        rebuilt = build_portfolio(pool, 10 + len(avoid), seed=4, attempts=8000)
        fresh = [l for l in rebuilt if ent.roster_key(l) not in avoid][:10]
        self.assertEqual(len(fresh), 10, "board should still fill after excluding")
        for l in fresh:
            self.assertNotIn(ent.roster_key(l), avoid)


class EntryPlan(unittest.TestCase):
    """Bug: the plan spent $27 of a $40 budget and stacked it on one seat."""

    def setUp(self):
        self.contests = json.loads((DATA / "lobby-week1-2026.json").read_text())["contests"]

    def test_rake_is_against_a_full_field(self):
        c = {"entryFee": 3, "totalPrizes": 400_000, "maxEntries": 158_730, "currentEntries": 40}
        self.assertAlmostEqual(rake_pct(c), 16.0, delta=0.5)

    def test_budget_is_spent_and_spread(self):
        entries, _ = plan(self.contests, 40, 10, "9/13 1:00p")
        self.assertEqual(len(entries), 10)
        spend = sum(e.fee for e in entries)
        self.assertLessEqual(spend, 40)
        self.assertGreaterEqual(spend, 36)
        self.assertLessEqual(max(e.fee for e in entries), 20, "one seat must not eat the week")

    def test_entry_limits_are_respected(self):
        entries, _ = plan(self.contests, 40, 10, "9/13 1:00p")
        used = {}
        for e in entries:
            used[e.contest["id"]] = used.get(e.contest["id"], 0) + 1
        for cid, n in used.items():
            cap = next(c["maxEntriesPerUser"] for c in self.contests if c["id"] == cid)
            self.assertLessEqual(n, cap)

    def test_every_contest_says_why_it_was_chosen(self):
        entries, _ = plan(self.contests, 40, 10, "9/13 1:00p")
        for e in entries:
            self.assertTrue(e.reason, "a contest with no stated reason is a black box")

    def test_only_this_slate(self):
        entries, _ = plan(self.contests, 40, 10, "9/13 1:00p")
        for e in entries:
            self.assertEqual(e.contest["lockTime"], "9/13 1:00p")

    def test_early_only_contests_are_not_offered(self):
        """
        The trap: "Early Only" contests lock at the same 1:00pm as the main
        slate but contain only the early games. Matching on lock time alone
        offers them, and a main-slate lineup cannot be entered in one.
        """
        early = [c for c in self.contests
                 if c.get("window") == "early" and c["lockTime"] == "9/13 1:00p"]
        self.assertTrue(early, "board should contain early-only contests to guard against")
        entries, _ = plan(self.contests, 40, 10, "9/13 1:00p")
        for e in entries:
            self.assertEqual(e.contest.get("window"), "main")

    def test_board_without_windows_still_plans(self):
        """An older capture has no window field; the filter must not empty it."""
        legacy = [{k: v for k, v in c.items() if k != "window"} for c in self.contests]
        entries, _ = plan(legacy, 40, 10, "9/13 1:00p")
        self.assertEqual(len(entries), 10)

    def test_small_budget_reduces_entries_and_says_so(self):
        entries, notes = plan(self.contests, 10, 10, "9/13 1:00p")
        self.assertLess(len(entries), 10)
        self.assertTrue(any("covers" in n for n in notes))


if __name__ == "__main__":
    unittest.main()


class PlacedEntries(unittest.TestCase):
    """
    The entries actually placed on DraftKings, read back into the Entry plan tab.

    The file is gitignored because this repo is public, so the app has to work
    without it -- a missing file must hide the section, not raise.
    """

    def test_a_missing_file_is_empty_not_an_error(self):
        data = ent.load_placed(Path("data/does-not-exist.json"))
        self.assertEqual(data["entries"], [])
        self.assertEqual(ent.placed_fees(data), 0)

    def test_malformed_json_is_empty_not_an_error(self):
        bad = Path("data/_bad-placed.json")
        bad.write_text("{ not json")
        try:
            self.assertEqual(ent.load_placed(bad)["entries"], [])
        finally:
            bad.unlink()

    def test_fees_sum_across_the_contests_each_entry_names(self):
        data = {"entries": [{"contestId": "a"}, {"contestId": "a"}, {"contestId": "b"}],
                "contests": [{"id": "a", "entryFee": 3}, {"id": "b", "entryFee": 5}]}
        self.assertEqual(ent.placed_fees(data), 11)

    def test_an_unknown_contest_id_contributes_nothing_rather_than_raising(self):
        data = {"entries": [{"contestId": "ghost"}], "contests": [{"id": "a", "entryFee": 3}]}
        self.assertEqual(ent.placed_fees(data), 0)
        self.assertEqual(ent.placed_contest(data, "ghost"), {})

    def test_matching_survives_the_screen_abbreviating_a_name(self):
        # The entry screen shows "B. Robinson"; the pool has "Bijan Robinson".
        placed = [{"name": "B. Robinson", "team": "ATL"}, {"name": "J. Love", "team": "GB"}]
        generated = [{"name": "Bijan Robinson", "team": "ATL"}, {"name": "Jordan Love", "team": "GB"}]
        self.assertEqual(ent.match_generated(placed, [generated]), 0)
        self.assertEqual(ent.overlap_with(placed, generated), 2)

    def test_same_surname_on_different_teams_is_not_a_match(self):
        placed = [{"name": "J. Lane", "team": "BAL"}]
        other = [{"name": "Jaylin Lane", "team": "WAS"}]
        self.assertIsNone(ent.match_generated(placed, [other]))
        self.assertEqual(ent.overlap_with(placed, other), 0)

    def test_no_generated_lineup_matches_returns_none_not_zero(self):
        placed = [{"name": "B. Robinson", "team": "ATL"}]
        self.assertIsNone(ent.match_generated(placed, [[{"name": "Saquon Barkley", "team": "PHI"}]]))

    def test_the_real_file_when_present_reconciles_to_its_budget(self):
        path = Path("data/entries-placed.json")
        if not path.exists():
            self.skipTest("placed-entries file not present (expected on a public deploy)")
        data = ent.load_placed(path)
        self.assertEqual(ent.placed_fees(data), data["staked"])
        for e in data["entries"]:
            roster = e["roster"]
            self.assertEqual(len(roster), 9, f"entry {e['entry']} is not nine players")
            self.assertEqual(sum(p["salary"] for p in roster),
                             50000 - e["remainingSalary"],
                             f"entry {e['entry']} does not reconcile to the cap")


class ParsePlaced(unittest.TestCase):
    """parse_placed takes text from an upload or from st.secrets, both untrusted."""

    def test_non_json_is_empty_not_an_exception(self):
        self.assertEqual(ent.parse_placed("not json at all")["entries"], [])

    def test_valid_json_of_the_wrong_shape_is_empty(self):
        # A JSON array parses fine but is not an entry record. Accepting it
        # would crash the tab further down instead of here.
        self.assertEqual(ent.parse_placed("[1, 2, 3]")["entries"], [])
        self.assertEqual(ent.parse_placed('{"entries": "six"}')["entries"], [])

    def test_missing_optional_keys_are_filled_in(self):
        d = ent.parse_placed('{"entries": []}')
        self.assertEqual(d["contests"], [])
        self.assertEqual(d["staked"], 0.0)
        self.assertIn("transcribed", d)

    def test_a_real_record_round_trips(self):
        path = Path("data/entries-placed.json")
        if not path.exists():
            self.skipTest("placed-entries file not present")
        d = ent.parse_placed(path.read_text())
        self.assertEqual(len(d["entries"]), len(ent.load_placed(path)["entries"]))
        self.assertEqual(ent.placed_fees(d), d["staked"])

    def test_the_empty_constant_is_not_shared_between_callers(self):
        # This caught a real bug: a module-level constant copied with dict()
        # is a SHALLOW copy, so every caller shared one `entries` list.
        a = ent.parse_placed("bad")
        a["entries"].append("x")
        self.assertEqual(ent.parse_placed("bad")["entries"], [])


class BoostersAreNotTournaments(unittest.TestCase):
    """
    The bug: the plan recommended "NFL $10K Super Booster [Top 10 Win $1,000]"
    at $50 -- half a $100 budget -- and the user could not find it on the board.

    DraftKings marks Double Ups with structure "double_up", which the planner
    already excluded. It marks Super Boosters "gpp", so they passed straight
    through. They pay a 2-3% cash rate against 20-25% for a real GPP, which is
    exactly what this project measured and recorded.
    """

    def test_the_contest_that_caused_this_is_caught(self):
        c = {"name": "NFL $10K Super Booster [Top 10 Win $1,000]", "entryFee": 50,
             "totalPrizes": 10000, "structure": "gpp", "maxEntries": 235}
        self.assertTrue(entry_mod.booster_shaped(c))

    def test_double_ups_are_caught_by_name_too(self):
        # Belt and braces: these are already excluded by structure, but the name
        # check must not depend on that field being right.
        self.assertTrue(entry_mod.booster_shaped({"name": "NFL GIANT $50 Double Up [Single Entry]"}))
        self.assertTrue(entry_mod.booster_shaped({"name": "NFL BIG 10x Booster [Top 30 Win $100]"}))

    def test_real_tournaments_are_not_caught(self):
        for name in ("NFL $400K Play-Action [20 Entry Max]",
                     "NFL $200K Red Zone [$25K to 1st, Single Entry]",
                     "NFL $3.5M Fantasy Football Millionaire",
                     "NFL $80K Shovel Pass [2x Min Cash]"):
            self.assertFalse(entry_mod.booster_shaped({"name": name}), name)

    def test_playable_drops_a_gpp_marked_booster(self):
        board = [
            {"id": "a", "name": "NFL $10K Super Booster [Top 10 Win $1,000]", "entryFee": 50,
             "totalPrizes": 10000, "structure": "gpp", "maxEntries": 235},
            {"id": "b", "name": "NFL $400K Play-Action [20 Entry Max]", "entryFee": 3,
             "totalPrizes": 400000, "structure": "gpp", "maxEntries": 158541},
        ]
        kept = [c["id"] for c in entry_mod.playable(board, None, None)]
        self.assertEqual(kept, ["b"])

    def test_the_plan_never_returns_a_booster_on_the_board_that_had_them(self):
        # The board shipped with the first release carried 32 of them.
        path = Path("data/lobby-week1-2026.json")
        board = json.loads(path.read_text())
        contests = board["contests"] if isinstance(board, dict) else board
        entries, _ = entry_mod.plan(contests, 100, 10, None, None)
        self.assertTrue(entries, "the plan should still produce entries")
        for e in entries:
            self.assertFalse(entry_mod.booster_shaped(e.contest), e.contest["name"])


class StatusCodeDialects(unittest.TestCase):
    """
    DraftKings writes the Status column in more than one dialect.

    Exports up to 2026-09-04 used single letters (O, Q, D, IR). The 09-13 export
    used full words (OUT, IR, Q, D). The parser mapped only the letters, so
    every one of the 159 players marked OUT in the newer file parsed as ACTIVE
    -- including Michael Penix Jr., ruled out after ACL surgery, who was then
    free to appear in generated lineups.
    """

    def test_both_dialects_map_to_the_same_status(self):
        for code in ("O", "OUT", "out", " Out "):
            self.assertEqual(pool_mod.read_status(code), "Out", code)
        for code in ("Q", "QUESTIONABLE", "GTD"):
            self.assertEqual(pool_mod.read_status(code), "Questionable", code)
        for code in ("D", "DOUBTFUL"):
            self.assertEqual(pool_mod.read_status(code), "Doubtful", code)
        self.assertEqual(pool_mod.read_status("IR"), "IR")

    def test_an_empty_status_is_active(self):
        for blank in ("", "   ", None):
            self.assertEqual(pool_mod.read_status(blank), "Active")

    def test_an_unknown_code_is_NOT_silently_active(self):
        # The whole bug in one assertion. A code we do not recognise must not
        # be read as "fine to play".
        s = pool_mod.read_status("SOMETHING_NEW")
        self.assertTrue(s.startswith("Unknown:"), s)
        self.assertFalse(rosterable({"status": s}))

    def test_a_player_marked_OUT_is_not_rosterable(self):
        rows = load_salaries("Position,Name + ID,Name,ID,Roster Position,Salary,"
                             "Game Info,TeamAbbrev,AvgPointsPerGame,Status\n"
                             "QB,X (1),Michael Penix Jr.,1,QB,4800,"
                             "ATL@PIT 09/13/2026 01:00PM ET,ATL,14.3,OUT\n")
        self.assertEqual(rows[0]["status"], "Out")
        self.assertFalse(rosterable(rows[0]))


class InjuryWireNameMatching(unittest.TestCase):
    """
    The wire and the salary file disagree about generational suffixes.

    DraftKings writes "Michael Penix Jr."; Sleeper writes "Michael Penix". The
    join key kept the suffix, so the two never met and nine players were
    invisible to the cross-check on the Week 1 board -- two of them rosterable
    in the app while the wire had them Out.
    """

    def test_a_suffix_does_not_break_the_join(self):
        self.assertEqual(injuries._norm("Michael Penix Jr."), injuries._norm("Michael Penix"))
        self.assertEqual(injuries._norm("Calvin Austin III"), injuries._norm("Calvin Austin"))
        self.assertEqual(injuries._norm("David Sills V"), injuries._norm("David Sills"))

    def test_different_players_still_key_differently(self):
        self.assertNotEqual(injuries._norm("Michael Penix"), injuries._norm("Michael Pittman"))

    def test_the_cross_check_finds_an_out_player_whose_file_says_questionable(self):
        rows = [{"id": "1", "name": "Michael Penix Jr.", "position": "QB", "team": "ATL",
                 "status": "Questionable"}]
        feed = {injuries._norm("Michael Penix"):
                {"status": "Out", "body_part": "Knee - ACL", "note": "Surgery", "team": "ATL"}}
        found = injuries.cross_check(rows, feed)
        self.assertEqual(len(found), 1, "the suffix must not hide an Out ruling")
        self.assertEqual(found[0]["live_status"], "Out")


class PayoutShape(unittest.TestCase):
    """
    Rake cannot tell a tournament from a cash game or a lottery.

    Two contests on the same Week 2 board, both $20, both 10.0% rake:
      NFL $20 50-50!                 pays 50% of the field at 1.8x
      NFL $20 200-Player (Top 3 Win) pays 3 of 200 at 36x

    The planner ranked on rake and wanted to put a whole budget into whichever
    small-field game kept the least, which on this board is a double-up.
    """

    def test_the_two_contests_that_motivated_this_classify_apart(self):
        du = shape.from_tiers(20, 100, [{"from": 1, "to": 50, "prize": 36}])
        lot = shape.from_tiers(20, 200, [{"from": 1, "to": 1, "prize": 1800},
                                         {"from": 2, "to": 2, "prize": 1080},
                                         {"from": 3, "to": 3, "prize": 720}])
        self.assertEqual(du["shape"], shape.DOUBLE_UP)
        self.assertEqual(lot["shape"], shape.LOTTERY)
        # The point: identical on every column the lobby shows.
        self.assertEqual(du["rake"], lot["rake"])

    def test_a_flat_tournament_is_not_playable_however_good_its_rake(self):
        # The $20 100-Player: 20% of the field paid, 1.8x minimum cash, 10.0%
        # rake -- the best rake on the Week 2 board. Cash rate and min cash both
        # say tournament. Its top prize is 13.5x the buy-in, so the upper tail a
        # stacked lineup exists to buy is worth almost nothing.
        flat = shape.from_tiers(20, 100, [
            {"from": 1, "to": 1, "prize": 270}, {"from": 2, "to": 2, "prize": 216},
            {"from": 3, "to": 3, "prize": 162}, {"from": 4, "to": 4, "prize": 144},
            {"from": 5, "to": 5, "prize": 126}, {"from": 6, "to": 6, "prize": 108},
            {"from": 7, "to": 8, "prize": 90}, {"from": 9, "to": 10, "prize": 72},
            {"from": 11, "to": 15, "prize": 54}, {"from": 16, "to": 20, "prize": 36}])
        self.assertEqual(flat["shape"], shape.FLAT_GPP)
        self.assertAlmostEqual(flat["cashRate"], 0.20)
        self.assertLess(flat["topPrizeMultiple"], shape.MIN_TOP_MULTIPLE)
        self.assertFalse(shape.playable_shape(flat["shape"]))

    def test_real_curves_reconcile_to_their_advertised_pools(self):
        # Every transcribed curve must sum to the prize pool DraftKings
        # advertises. A curve that does not is a transcription error, and it
        # would otherwise produce confident, wrong rake and cash-rate figures.
        import json as _json
        from pathlib import Path as _Path
        f = _Path("data/payouts-week2.json")
        if not f.exists():
            self.skipTest("no payout curves shipped")
        for name, c in _json.loads(f.read_text()).items():
            r = shape.from_tiers(c["entryFee"], c["maxEntries"], c["tiers"])
            self.assertAlmostEqual(r["prizePool"], c["prizePool"], places=2,
                                   msg=f"{name} curve does not sum to its pool")

    def test_a_standard_gpp_classifies_as_one(self):
        gpp = shape.from_tiers(27, 4319,
                               [{"from": 1, "to": 1, "prize": 10000},
                                {"from": 2, "to": 987, "prize": 54}])
        self.assertEqual(gpp["shape"], shape.GPP)
        self.assertTrue(shape.playable_shape(gpp["shape"]))

    def test_neither_a_double_up_nor_a_lottery_is_playable(self):
        self.assertFalse(shape.playable_shape(shape.DOUBLE_UP))
        self.assertFalse(shape.playable_shape(shape.LOTTERY))

    def test_an_unknown_curve_is_refused_not_assumed(self):
        # The entire reason this module exists: 21 contests on the Week 2 board
        # were marked gpp on an assumption before anyone had seen their curves.
        self.assertEqual(shape.classify(None, None), shape.UNKNOWN)
        self.assertEqual(shape.classify(0.2, None), shape.UNKNOWN)
        self.assertEqual(shape.from_tiers(20, 100, [])["shape"], shape.UNKNOWN)
        self.assertFalse(shape.playable_shape(shape.UNKNOWN))

    def test_the_planner_refuses_a_contest_of_unknown_shape(self):
        board = [
            {"id": "a", "name": "Mystery 100-Player", "entryFee": 10, "totalPrizes": 900,
             "maxEntries": 100, "structure": "unknown", "window": "main", "lockTime": None},
            {"id": "b", "name": "Real GPP", "entryFee": 10, "totalPrizes": 9000,
             "maxEntries": 1000, "structure": "gpp", "window": "main", "lockTime": None},
        ]
        kept = [c["id"] for c in entry_mod.playable(board, None, None)]
        self.assertEqual(kept, ["b"])


class ContestSelection(unittest.TestCase):
    """
    Scoring contests on what was measured, not on rake alone.

    fv/entry.py ranked on rake and put ten entries in one contest -- and before
    the payout curves arrived, ten entries in a double-up.
    """

    def _c(self, **kw):
        base = {"id": "x", "name": "Test GPP", "entryFee": 12, "totalPrizes": 100000,
                "entered": 5000, "maxEntries": 9804, "maxEntriesPerUser": 1,
                "structure": "gpp", "window": "main", "topPrizeMultiple": 800}
        base.update(kw)
        return base

    def test_a_flat_tournament_is_refused_however_good_its_rake(self):
        # 10% rake, the best on the Week 2 board, topping out at 14x.
        self.assertIsNone(select.score(self._c(structure="flat_gpp", topPrizeMultiple=13.5)))

    def test_double_ups_lotteries_and_unknowns_are_refused(self):
        for s in ("double_up", "lottery", "unknown"):
            self.assertIsNone(select.score(self._c(structure=s)), s)

    def test_a_tiny_field_is_refused(self):
        self.assertIsNone(select.score(self._c(maxEntries=100)))

    def test_lower_rake_scores_higher(self):
        cheap = select.score(self._c(id="a", totalPrizes=105000))   # less kept
        dear = select.score(self._c(id="b", totalPrizes=90000))
        self.assertGreater(cheap.score, dear.score)

    def test_a_bigger_top_prize_scores_higher(self):
        big = select.score(self._c(id="a", topPrizeMultiple=1250))
        small = select.score(self._c(id="b", topPrizeMultiple=100))
        self.assertGreater(big.score, small.score)

    def test_an_unknown_top_prize_never_scores_as_well_as_a_known_good_one(self):
        # The $20 100-Player looked excellent on every column that WAS known.
        unknown = select.score(self._c(id="a", topPrizeMultiple=None))
        known = select.score(self._c(id="b", topPrizeMultiple=1250))
        self.assertGreater(known.score, unknown.score)

    def test_single_entry_scores_higher_than_multi(self):
        single = select.score(self._c(id="a", maxEntriesPerUser=1))
        multi = select.score(self._c(id="b", maxEntriesPerUser=150))
        self.assertGreater(single.score, multi.score)

    def test_the_budget_spreads_across_contests_before_repeating_one(self):
        board = [self._c(id=f"c{i}", name=f"GPP {i}", entryFee=10, maxEntriesPerUser=20)
                 for i in range(5)]
        placed, _ = select.build(board, 50, 5)
        self.assertEqual(len({p["contest"]["id"] for p in placed}), 5,
                         "five entries across five contests, not five in one")

    def test_it_never_overspends_the_budget(self):
        board = [self._c(id=f"c{i}", name=f"GPP {i}", entryFee=27) for i in range(10)]
        placed, _ = select.build(board, 100, 10)
        self.assertLessEqual(sum(p["fee"] for p in placed), 100)

    def test_an_empty_board_says_so_rather_than_returning_nothing_quietly(self):
        placed, notes = select.build([self._c(structure="unknown")], 100, 10)
        self.assertEqual(placed, [])
        self.assertTrue(notes and "payout shape" in notes[0])


class SliderDefaults(unittest.TestCase):
    """
    The RB cap default sat at 75%, above the point where it binds.

    On the Week 2 2026 slate no running back reached more than 6 of 10 lineups
    unprompted, so every setting from 60% up produced an identical portfolio --
    the slider looked broken because moving it around the default genuinely
    changed nothing.
    """

    def test_rb_default_is_inside_the_binding_range(self):
        # At the usual 10 lineups the cap must be low enough to bite.
        self.assertLess(rules.RB_EXPOSURE_PCT, 60)
        self.assertGreaterEqual(rules.RB_EXPOSURE_PCT, 10)

    def test_other_positions_keep_their_own_cap(self):
        # Changing the RB slider default must not retune WR/TE/DST, which have
        # no slider and fall back to EXPOSURE_PCT.
        self.assertNotEqual(rules.RB_EXPOSURE_PCT, rules.EXPOSURE_PCT)
        self.assertEqual(rules.EXPOSURE_PCT, 75)

    def test_ceiling_weight_default_is_in_range(self):
        self.assertGreaterEqual(rules.CEILING_WEIGHT_DEFAULT, 0.0)
        self.assertLessEqual(rules.CEILING_WEIGHT_DEFAULT, 1.0)

    def test_rb_cap_actually_changes_the_portfolio_at_the_default(self):
        """A default that cannot change anything is the bug this replaced."""
        data = Path(__file__).parent / "data"
        rows = load_salaries((data / "DKSalaries.csv").read_text())
        pool = [r for r in keep_starting_quarterbacks(rows) if rosterable(r)]
        # The builder reads each player's ceiling, so the pool must be prepared
        # the same way the app prepares it.
        pool = apply_ceilings(pool, load_json(data / "player-variance.json"))
        loose = build_portfolio(pool, 10, seed=1, rb_exposure=100)
        tight = build_portfolio(pool, 10, seed=1, rb_exposure=rules.RB_EXPOSURE_PCT)
        self.assertNotEqual([ent.roster_key(l) for l in loose],
                            [ent.roster_key(l) for l in tight])


class ContestSpreading(unittest.TestCase):
    """All ten lineups were assigned to one tournament, which read as a bug."""

    def _lobby(self):
        return json.loads((Path(__file__).parent / "data" / "lobby-week2-2026.json").read_text())

    def test_distinct_contests_are_filled_before_one_is_reused(self):
        contests = self._lobby()["contests"]
        entries, _ = plan(contests, 200, 10)
        if len(entries) < 2:
            self.skipTest("budget funded fewer than two entries")
        # No contest may take a second entry while another at the same fee is free.
        used = {}
        for e in entries:
            used[e.contest["id"]] = used.get(e.contest["id"], 0) + 1
        for e in entries:
            if used[e.contest["id"]] < 2:
                continue
            same_fee = {c["id"] for c in contests
                        if c.get("structure") == "gpp" and c.get("window") == "main"
                        and c["entryFee"] == e.fee}
            unused = same_fee - set(used)
            self.assertEqual(unused, set(),
                             f"{e.contest['name']} reused while {unused} were free")

    def test_concentration_is_explained_rather_than_silent(self):
        entries, notes = plan(self._lobby()["contests"], 100, 10)
        per = {}
        for e in entries:
            per[e.contest["name"]] = per.get(e.contest["name"], 0) + 1
        if max(per.values(), default=0) < 2:
            self.skipTest("this budget did not concentrate")
        self.assertTrue(any("same tournament" in n for n in notes),
                        "concentration happened with no note explaining it")


class ContestCapacity(unittest.TestCase):
    """
    Offering a contest that cannot accept the lineup is worse than offering
    nothing: DraftKings rejects it at submission, after you think you are done.
    """

    CONTESTS = [
        {"id": "single", "name": "Single", "entryFee": 27, "totalPrizes": 50000,
         "maxEntriesPerUser": 1, "entered": 10, "maxEntries": 100},
        {"id": "three", "name": "Three max", "entryFee": 15, "totalPrizes": 5000,
         "maxEntriesPerUser": 3, "entered": 10, "maxEntries": 100},
        {"id": "full", "name": "Sold out", "entryFee": 10, "totalPrizes": 900,
         "maxEntriesPerUser": 150, "entered": 100, "maxEntries": 100},
    ]

    def _entered(self, *contest_ids):
        return {f"key{i}": {"contest_id": c, "contest": c, "fee": 1.0, "players": []}
                for i, c in enumerate(contest_ids)}

    def test_a_full_contest_is_never_offered(self):
        ids = [c["id"] for c in ent.contests_with_room(self.CONTESTS, {})]
        self.assertNotIn("full", ids)

    def test_single_entry_drops_out_once_used(self):
        got = ent.contests_with_room(self.CONTESTS, self._entered("single"))
        self.assertNotIn("single", [c["id"] for c in got])

    def test_multi_entry_survives_until_its_limit(self):
        for used in range(3):
            got = ent.contests_with_room(self.CONTESTS, self._entered(*(["three"] * used)))
            self.assertIn("three", [c["id"] for c in got], f"gone after {used} entries")
        got = ent.contests_with_room(self.CONTESTS, self._entered("three", "three", "three"))
        self.assertNotIn("three", [c["id"] for c in got])

    def test_entries_left_counts_down(self):
        three = self.CONTESTS[1]
        self.assertEqual(ent.entries_left(three, {}), 3)
        self.assertEqual(ent.entries_left(three, self._entered("three")), 2)
        self.assertEqual(ent.entries_left(three, self._entered("three", "three", "three")), 0)

    def test_cheapest_first(self):
        got = ent.contests_with_room(self.CONTESTS, {})
        self.assertEqual([c["entryFee"] for c in got], sorted(c["entryFee"] for c in got))


class UsedQuarterbacks(unittest.TestCase):
    """A quarterback already staked must not come back on the board."""

    POOL = [
        {"id": "1", "name": "Jordan Love", "position": "QB", "team": "GB", "projection": 20.0},
        {"id": "2", "name": "Caleb Williams", "position": "QB", "team": "CHI", "projection": 22.0},
        {"id": "3", "name": "Bryce Young", "position": "QB", "team": "CAR", "projection": 18.0},
        {"id": "4", "name": "Javonte Williams", "position": "RB", "team": "DAL", "projection": 15.0},
        {"id": "5", "name": "Christian Watson", "position": "WR", "team": "GB", "projection": 12.0},
    ]

    def test_reads_both_records(self):
        placed = {"entries": [{"roster": [{"slot": "QB", "name": "J. Love", "team": "GB"}]}]}
        session = {"k": {"players": [{"position": "QB", "name": "Caleb Williams", "team": "CHI"}]}}
        used = ent.quarterbacks_used(placed, session)
        self.assertEqual(used, {("j", "love", "GB"), ("c", "williams", "CHI")})

    def test_abbreviated_and_full_names_match(self):
        placed = {"entries": [{"roster": [{"slot": "QB", "name": "J. Love", "team": "GB"}]}]}
        left = ent.without_used_quarterbacks(self.POOL, ent.quarterbacks_used(placed, {}))
        self.assertNotIn("Jordan Love", [p["name"] for p in left])

    def test_the_team_disambiguates_two_williamses(self):
        """C. Williams is Caleb at CHI; Javonte Williams at DAL must survive."""
        placed = {"entries": [{"roster": [{"slot": "QB", "name": "C. Williams", "team": "CHI"}]}]}
        left = ent.without_used_quarterbacks(self.POOL, ent.quarterbacks_used(placed, {}))
        names = [p["name"] for p in left]
        self.assertNotIn("Caleb Williams", names)
        self.assertIn("Javonte Williams", names)

    def test_only_quarterbacks_are_dropped(self):
        placed = {"entries": [{"roster": [
            {"slot": "QB", "name": "J. Love", "team": "GB"},
            {"slot": "WR", "name": "C. Watson", "team": "GB"}]}]}
        left = ent.without_used_quarterbacks(self.POOL, ent.quarterbacks_used(placed, {}))
        self.assertIn("Christian Watson", [p["name"] for p in left])

    def test_nothing_entered_changes_nothing(self):
        self.assertEqual(ent.without_used_quarterbacks(self.POOL, set()), self.POOL)


class BlendBeforeCeilings(unittest.TestCase):
    """
    The ceiling is a multiple of the projection, so it must be derived AFTER
    the blend. Doing it first leaves every ceiling scaled to DraftKings'
    one-game average, and at ceiling weight 1.0 the builder uses only that.
    """

    def test_ceiling_tracks_the_blended_projection(self):
        data = Path(__file__).parent / "data"
        rows = load_salaries((data / "DKSalaries.csv").read_text())
        rows = [r for r in keep_starting_quarterbacks(rows) if rosterable(r)]
        prior = blend_mod.load_prior(data / "prior-season-2025.json")
        variance = load_json(data / "player-variance.json")

        wrong = apply_ceilings([dict(r) for r in rows], variance)          # ceilings first
        wrong = blend_mod.apply_blend(wrong, prior, 2)                      # then blend
        right = apply_ceilings(blend_mod.apply_blend([dict(r) for r in rows], prior, 2), variance)

        by_wrong = {r["name"]: r for r in wrong}
        drifted = [n for n, r in ((p["name"], p) for p in right)
                   if abs(by_wrong[n]["ceiling"] - r["ceiling"]) > 0.01]
        self.assertTrue(drifted, "the two orders should differ in week 2")
        # In the correct order every ceiling is a multiple of its own projection.
        for r in right:
            if r["projection"] > 0:
                self.assertGreaterEqual(r["ceiling"] / r["projection"], 1.14, r["name"])


class PlacedEntriesReconcile(unittest.TestCase):
    """
    Every recorded entry must be a lineup DraftKings would actually have
    accepted, and every player must exist in the salary file at the salary the
    screenshot showed. These are hand-transcribed from screens; a wrong digit is
    silent and would quietly corrupt every later analysis of the week.
    """

    SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]

    def setUp(self):
        data = Path(__file__).parent / "data"
        f = data / "entries-placed.json"
        if not f.exists():
            self.skipTest("no placed entries shipped")
        self.d = json.loads(f.read_text())
        rows = load_salaries((data / "DKSalaries.csv").read_text())
        self.by = {}
        for r in rows:
            parts = r["name"].replace(".", "").split()
            self.by.setdefault((parts[0][0].lower(), parts[-1].lower(), r["team"]), []).append(r)

    def test_every_roster_is_a_legal_lineup(self):
        for e in self.d["entries"]:
            with self.subTest(entry=e["entry"]):
                self.assertEqual(len(e["roster"]), 9)
                self.assertEqual([p["slot"] for p in e["roster"]], self.SLOTS)
                total = sum(p["salary"] for p in e["roster"])
                self.assertEqual(total + e["remainingSalary"], 50000)
                self.assertLessEqual(total, 50000)
                # DraftKings requires at least two games represented.
                self.assertGreaterEqual(len({p["team"] for p in e["roster"]}), 2)

    def test_every_player_exists_at_that_salary(self):
        for e in self.d["entries"]:
            for p in e["roster"]:
                if p["slot"] == "DST":
                    continue
                with self.subTest(entry=e["entry"], player=p["name"]):
                    parts = p["name"].replace(".", "").split()
                    hits = self.by.get((parts[0][0].lower(), parts[-1].lower(), p["team"]), [])
                    self.assertTrue(hits, f'{p["name"]} ({p["team"]}) is not in the salary file')
                    self.assertTrue(any(h["salary"] == p["salary"] for h in hits),
                                    f'{p["name"]}: recorded ${p["salary"]}, file has '
                                    f'{[h["salary"] for h in hits]}')

    def test_staked_equals_the_sum_of_entry_fees(self):
        fees = {c["id"]: c["entryFee"] for c in self.d["contests"]}
        self.assertEqual(sum(fees[e["contestId"]] for e in self.d["entries"]), self.d["staked"])

    def test_every_entry_points_at_a_known_contest(self):
        ids = {c["id"] for c in self.d["contests"]}
        for e in self.d["entries"]:
            self.assertIn(e["contestId"], ids)

    def test_single_entry_contests_hold_one_entry(self):
        counts = {}
        for e in self.d["entries"]:
            counts[e["contestId"]] = counts.get(e["contestId"], 0) + 1
        for c in self.d["contests"]:
            used = counts.get(c["id"], 0)
            self.assertLessEqual(used, c.get("entriesPerUser", 1),
                                 f'{c["name"]} holds {used} of {c.get("entriesPerUser")} allowed')

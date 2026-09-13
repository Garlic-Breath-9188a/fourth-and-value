"""
Tests for the rules that came from real bugs.

Each test names the bug it prevents. They are not coverage; they are the
specific things that were wrong on screen at some point and were fixed.
Run with:  python3 -m unittest test_rules -v
"""
import csv, io, json, unittest
from datetime import datetime
from pathlib import Path

from fv import rules
from fv import entered as ent
from fv.slate import parse_kickoff, kickoff_windows, slate_options, main_slate, restrict, in_slate
from fv.pool import load_salaries, keep_starting_quarterbacks, apply_ceilings, rosterable, load_json
from fv.roster import order_roster, stack_role, is_stacked, fill_dk_template, DK_SLOTS
from fv.optimize import build_portfolio, build_lineup
from fv.entry import plan, rake_pct
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
        _, slate = live_pool()
        self.assertEqual(slate.locks_at, datetime(2026, 9, 13, 13, 0))
        self.assertEqual(slate.game_count, 12)

    def test_slate_is_bounded_at_the_top(self):
        """The original bug: no end bound, so Monday night was 'after the lock'."""
        _, slate = live_pool()
        self.assertFalse(in_slate("DEN@KC 09/14/2026 08:15PM ET", slate.locks_at, slate.ends_at))

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

    def _qb(self, pool, surname):
        return next(p for p in pool if surname in p["name"] and p["position"] == "QB")

    def test_must_play_qb_appears_once_at_a_tight_cap(self):
        pool, _ = live_pool()
        qb = self._qb(pool, "Hurts")
        lineups = build_portfolio(pool, 10, seed=1, attempts=8000,
                                  qb_exposure=10, must_play={qb["id"]})
        holding = [l for l in lineups if any(p["id"] == qb["id"] for p in l)]
        self.assertEqual(len(holding), 1, "must-play means somewhere, not everywhere")

    def test_exposure_cap_still_binds_with_a_must_play(self):
        pool, _ = live_pool()
        qb = self._qb(pool, "Hurts")
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
        picks = {self._qb(pool, "Hurts")["id"]}
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
        self.assertEqual(ent.placed_fees(data), data["budget"])
        for e in data["entries"]:
            roster = e["roster"]
            self.assertEqual(len(roster), 9, f"entry {e['entry']} is not nine players")
            self.assertEqual(sum(p["salary"] for p in roster),
                             50000 - e["remainingSalary"],
                             f"entry {e['entry']} does not reconcile to the cap")

"""
Tests for the rules that came from real bugs.

Each test names the bug it prevents. They are not coverage; they are the
specific things that were wrong on screen at some point and were fixed.
Run with:  python3 -m unittest test_rules -v
"""
import json, unittest
from datetime import datetime
from pathlib import Path

from fv import rules
from fv.slate import parse_kickoff, kickoff_windows, slate_options, main_slate, restrict, in_slate
from fv.pool import load_salaries, keep_starting_quarterbacks, apply_ceilings, rosterable, load_json
from fv.roster import order_roster, stack_role, is_stacked, DK_SLOTS
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

    def test_only_this_slate(self):
        entries, _ = plan(self.contests, 40, 10, "9/13 1:00p")
        for e in entries:
            self.assertEqual(e.contest["lockTime"], "9/13 1:00p")

    def test_small_budget_reduces_entries_and_says_so(self):
        entries, notes = plan(self.contests, 10, 10, "9/13 1:00p")
        self.assertLess(len(entries), 10)
        self.assertTrue(any("covers" in n for n in notes))


if __name__ == "__main__":
    unittest.main()

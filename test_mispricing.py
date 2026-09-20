"""
The mispricing board.

These pin the invariants that were got wrong while building it, not the numbers
for one slate: next week's file will not have the same players and a test that
hard-codes them fails for the wrong reason.

Run with:  python3 -m unittest test_mispricing -v
"""
import json, unittest
from pathlib import Path

from fv import mispricing as mp
from fv.pool import load_salaries, keep_starting_quarterbacks, rosterable

DATA = Path(__file__).parent / "data"


def _usage():
    return json.loads((DATA / "usage-2026.json").read_text())


def _pool():
    rows = load_salaries((DATA / "DKSalaries.csv").read_text())
    return [r for r in keep_starting_quarterbacks(rows) if rosterable(r)]


class PriceLine(unittest.TestCase):
    def test_covers_only_fitted_positions(self):
        # The workbook has no DST rows, so there is no DST line to fit. If one
        # ever appears here it was guessed rather than measured.
        self.assertEqual(set(mp.PRICE_LINE), {"QB", "RB", "TE", "WR"})
        self.assertIsNone(mp.price_implied("DST", 3000))

    def test_points_rise_with_price(self):
        for pos, line in mp.PRICE_LINE.items():
            self.assertGreater(line["slope"], 0, pos)
            self.assertGreater(line["resid_sd"], 0, pos)
            self.assertGreater(mp.price_implied(pos, 8000), mp.price_implied(pos, 4000), pos)


class Edge(unittest.TestCase):
    def test_none_not_zero_for_unfitted_position(self):
        # Zero is a real edge and would rank a defence mid-board.
        self.assertIsNone(mp.edge_sd("DST", 50, 0))
        self.assertIsNone(mp.edge_points("DST", 50, 0))

    def test_rises_with_usage(self):
        self.assertGreater(mp.edge_points("RB", 80, 20), mp.edge_points("RB", 30, 4))
        self.assertGreater(mp.edge_points("WR", 90, 8), mp.edge_points("WR", 90, 2))


class Board(unittest.TestCase):
    def test_accepts_camel_and_snake_case(self):
        """The usage file is written by the TypeScript build, in camelCase."""
        rows = [{"name": "Test Player", "position": "RB", "team": "XX",
                 "salary": 5000, "projection": 10.0}]
        camel = {"players": {"testplayer": {"snapPct": 80, "touches": 18, "games": 1, "week": 1}}}
        snake = {"players": {"testplayer": {"snap_pct": 80, "touches": 18, "games": 1, "week": 1}}}
        a, b = mp.board(rows, camel), mp.board(rows, snake)
        self.assertEqual(len(a["ranked"]), 1)
        self.assertEqual(len(b["ranked"]), 1)
        self.assertEqual(a["ranked"][0]["edge_points"], b["ranked"][0]["edge_points"])

    def test_unknown_touches_are_not_zero(self):
        """A missing stat line must not score as a confident fade."""
        rows = [{"name": "Blocking Te", "position": "TE", "team": "XX",
                 "salary": 4000, "projection": 4.0}]
        usage = {"players": {"blockingte": {"snapPct": 60, "touches": None, "games": 1}}}
        board = mp.board(rows, usage)
        self.assertEqual(board["ranked"], [])
        self.assertEqual(len(board["unranked"]), 1)
        self.assertIn("unknown", board["unranked"][0]["why"])

    def test_missing_usage_file_does_not_crash(self):
        rows = _pool()
        board = mp.board(rows, None)
        self.assertEqual(board["ranked"], [])
        self.assertEqual(len(board["unranked"]), len(rows))

    def test_every_player_is_ranked_or_explained(self):
        """A board that silently drops players is the failure this project catches."""
        rows = _pool()
        board = mp.board(rows, _usage())
        self.assertEqual(len(board["ranked"]) + len(board["unranked"]), len(rows))
        self.assertTrue(all(u["why"] for u in board["unranked"]))

    def test_targets_are_cheap_and_fades_are_dear(self):
        board = mp.board(_pool(), _usage())
        self.assertTrue(board["targets"], "no targets on a real slate")
        self.assertTrue(board["fades"], "no fades on a real slate")
        self.assertTrue(all(r["cheap"] for r in board["targets"]))
        self.assertTrue(all(not r["cheap"] for r in board["fades"]))

    def test_signs_are_consistent_with_the_label(self):
        """Nothing labelled underpriced may carry a negative edge, or the reverse."""
        board = mp.board(_pool(), _usage())
        self.assertTrue(all(r["edge_points"] > 0 for r in board["targets"]))
        self.assertTrue(all(r["edge_points"] < 0 for r in board["fades"]))

    def test_no_single_position_fills_a_list(self):
        """
        Quarterbacks all sit at 100% snap share. Selecting across positions let
        them take every slot on the target list, which said more about the
        position than about the price.
        """
        board = mp.board(_pool(), _usage())
        for name in ("targets", "fades"):
            positions = [r["position"] for r in board[name]]
            if len(positions) >= 5:
                top = max(set(positions), key=positions.count)
                self.assertLess(positions.count(top), len(positions), f"{name} is all {top}")

    def test_support_matches_the_measured_intervals(self):
        board = mp.board(_pool(), _usage())
        self.assertEqual(set(board["target_support"]), {"QB", "RB", "WR"})
        self.assertEqual(set(board["fade_support"]), {"RB", "WR"})
        self.assertTrue(all(isinstance(r["measured"], bool)
                            for r in board["targets"] + board["fades"]))


class Measured(unittest.TestCase):
    def test_every_figure_states_its_uncertainty(self):
        for k in ("cheap_top", "cheap_bottom", "dear_top", "dear_bottom"):
            lo, hi = mp.MEASURED[k]["ci"]
            self.assertLess(lo, hi, k)

    def test_null_results_stay_recorded_as_null(self):
        # The dear-half top fifth measured nothing.
        lo, hi = mp.MEASURED["dear_top"]["ci"]
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)
        # So did DraftKings' own price movement, the original hypothesis.
        lo, hi = mp.MEASURED["price_movement_ci"]
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)
        # And TE on the target side.
        lo, hi = mp.MEASURED["cheap_top_by_position"]["TE"]["ci"]
        self.assertLess(lo, 0)


if __name__ == "__main__":
    unittest.main()

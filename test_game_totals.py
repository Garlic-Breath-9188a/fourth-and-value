"""
The Vegas game-total ceiling adjustment.

Run with:  python3 -m unittest test_game_totals -v

The measured effect is small on purpose. Backtested on 11,401 player-weeks
(2022-2025) the closing total does not move expected points at all -- +0.33 per
player, 1.5 sd, and null in every position separately. It moves the far tail:
at equal projection the rate of a 30+ point game runs 2.7% in low-total games
against 5.2% in high-total ones. End to end that is 1.03x on the 85th
percentile, which is the ceiling we actually use.

These tests exist mostly to stop the adjustment from GROWING. A bug that scaled
it up would reorder the board on a signal measured to be worth almost nothing.
"""
import unittest

from fv.pool import apply_ceilings, game_total_multiplier, LEAGUE_MEAN_TOTAL


def player(team="BUF", pos="WR", proj=12.0):
    return {"name": "A Player", "team": team, "position": pos, "projection": proj}


class Multiplier(unittest.TestCase):
    def test_a_missing_line_is_not_an_average_line(self):
        self.assertEqual(game_total_multiplier(None), 1.0)

    def test_direction(self):
        self.assertGreater(game_total_multiplier(52.0), 1.0)
        self.assertLess(game_total_multiplier(36.0), 1.0)
        self.assertEqual(game_total_multiplier(LEAGUE_MEAN_TOTAL), 1.0)

    def test_it_stays_small(self):
        for total in (20.0, 30.0, 60.0, 80.0):
            self.assertGreaterEqual(game_total_multiplier(total), 0.96)
            self.assertLessEqual(game_total_multiplier(total), 1.04)

    def test_swing_across_a_real_slate_stays_under_five_percent(self):
        # Week 3 2026 ran 37.5 (TEN@NYG) to 53.5 (BAL@DAL).
        lo, hi = game_total_multiplier(37.5), game_total_multiplier(53.5)
        self.assertLess(hi / lo, 1.05)


class Applied(unittest.TestCase):
    TOTALS = {"teams": {"BUF": 53.5, "NYG": 37.5}}

    def test_no_totals_leaves_the_ceiling_alone(self):
        rows = apply_ceilings([player()], None, None)
        self.assertIsNone(rows[0]["game_total"])
        self.assertNotIn("game total", rows[0]["ceiling_source"])

    def test_the_mean_is_never_touched(self):
        """The backtest says the total carries NO information about expected
        points. If this ever fails, the adjustment has been put in the wrong
        place."""
        rows = apply_ceilings([player(team="BUF"), player(team="NYG")], None, self.TOTALS)
        self.assertEqual(rows[0]["projection"], 12.0)
        self.assertEqual(rows[1]["projection"], 12.0)

    def test_high_total_gets_the_higher_ceiling(self):
        rows = apply_ceilings([player(team="BUF"), player(team="NYG")], None, self.TOTALS)
        self.assertGreater(rows[0]["ceiling"], rows[1]["ceiling"])
        self.assertIn("game total 53.5", rows[0]["ceiling_source"])

    def test_a_team_not_on_the_slate_is_not_penalised(self):
        rows = apply_ceilings([player(team="ZZZ")], None, self.TOTALS)
        self.assertIsNone(rows[0]["game_total"])
        self.assertNotIn("game total", rows[0]["ceiling_source"])


if __name__ == "__main__":
    unittest.main()

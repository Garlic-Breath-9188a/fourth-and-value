"""
fv.matchup.attach: read-only lookup of defence-vs-position by opponent.

Run with:  python3 -m unittest test_matchup -v
"""
import unittest

from fv import matchup


def data(**teams_by_pos):
    return {"positions": {pos: {"teams": teams} for pos, teams in teams_by_pos.items()}}


class Attach(unittest.TestCase):
    def test_a_covered_opponent_gets_the_three_fields(self):
        rows = [{"id": "p1", "position": "RB", "opponent": "DET"}]
        d = data(RB={"DET": {"rank": 1, "versusAverage": 11.8, "games": 5}})
        out = matchup.attach(rows, d)
        self.assertEqual(out[0]["matchup_rank"], 1)
        self.assertEqual(out[0]["matchup_vs_avg"], 11.8)
        self.assertEqual(out[0]["matchup_games"], 5)

    def test_an_uncovered_opponent_is_left_alone(self):
        rows = [{"id": "p1", "position": "RB", "opponent": "ZZZ"}]
        d = data(RB={"DET": {"rank": 1, "versusAverage": 11.8, "games": 5}})
        out = matchup.attach(rows, d)
        self.assertNotIn("matchup_rank", out[0])

    def test_dst_has_no_position_table_and_is_left_alone(self):
        rows = [{"id": "p1", "position": "DST", "opponent": "DET"}]
        d = data(RB={"DET": {"rank": 1, "versusAverage": 11.8, "games": 5}})
        out = matchup.attach(rows, d)
        self.assertNotIn("matchup_rank", out[0])

    def test_missing_data_leaves_every_row_alone(self):
        rows = [{"id": "p1", "position": "RB", "opponent": "DET"}]
        out = matchup.attach(rows, None)
        self.assertNotIn("matchup_rank", out[0])

    def test_the_original_rows_are_not_mutated(self):
        rows = [{"id": "p1", "position": "RB", "opponent": "DET"}]
        d = data(RB={"DET": {"rank": 1, "versusAverage": 11.8, "games": 5}})
        matchup.attach(rows, d)
        self.assertNotIn("matchup_rank", rows[0])


if __name__ == "__main__":
    unittest.main()

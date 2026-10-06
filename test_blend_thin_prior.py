"""
The prior season is weighted by its own sample size.

Run with:  python3 -m unittest test_blend_thin_prior -v

The blend exists because one current-season game cannot carry a projection.
It had the same flaw on the other side: the prior got a flat 1 - n/(n+k)
whether it rested on 1 game or 17. Measured over 24,933 player-weeks,
2019-2025: MAE 5.335 -> 5.319 overall (CI -0.024 to -0.007), and 4.457 ->
4.316 on the player-weeks whose prior is 1-2 games (CI -0.255 to -0.028).

These tests guard the SHAPE, not the constant: a thin prior must count for
less than a full one, a full prior must be close to what it always was, and
a caller who does not pass prior_games must get the old answer.
"""
import unittest

from fv.blend import blend_value, current_weight, SHRINKAGE_K


class ThinPrior(unittest.TestCase):
    # three games this season averaging 0, prior season averaging 12
    CUR, N, PRIOR = 0.0, 3, 12.0

    def test_omitting_prior_games_keeps_the_old_answer(self):
        """A caller that cannot supply the sample size is not silently given a
        different number than it used to get."""
        w = current_weight(self.N, SHRINKAGE_K)
        self.assertAlmostEqual(blend_value(self.CUR, self.N, self.PRIOR),
                               w * self.CUR + (1 - w) * self.PRIOR)

    def test_a_one_game_prior_counts_for_less_than_a_full_one(self):
        thin = blend_value(self.CUR, self.N, self.PRIOR, prior_games=1)
        full = blend_value(self.CUR, self.N, self.PRIOR, prior_games=17)
        self.assertLess(thin, full)

    def test_a_full_prior_is_close_to_the_unweighted_blend(self):
        """17 games should barely be discounted; the fix must not quietly
        reprice every established player."""
        full = blend_value(self.CUR, self.N, self.PRIOR, prior_games=17)
        old = blend_value(self.CUR, self.N, self.PRIOR)
        self.assertLess(abs(full - old), 1.0)

    def test_the_thin_case_moves_toward_this_season(self):
        """Ronnie Bell: 0.0 across three games this season, one 2025 game at
        10.3, projected 5.2 and scored 0.00 in Week 4 2026."""
        v = blend_value(0.0, 3, 10.3, prior_games=1)
        self.assertLess(v, 2.0)

    def test_monotonic_in_prior_sample(self):
        vals = [blend_value(self.CUR, self.N, self.PRIOR, prior_games=m)
                for m in (1, 2, 4, 8, 17)]
        self.assertEqual(vals, sorted(vals))

    def test_a_missing_side_still_short_circuits(self):
        self.assertIsNone(blend_value(None, 0, None))
        self.assertEqual(blend_value(None, 0, 9.0, prior_games=1), 9.0)
        self.assertEqual(blend_value(7.0, 3, None, prior_games=1), 7.0)


if __name__ == "__main__":
    unittest.main()

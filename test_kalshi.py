"""
The Kalshi ladder maths.

Run with:  python3 -m unittest test_kalshi -v
"""
import json, unittest
from pathlib import Path

from fv import kalshi

DATA = Path(__file__).parent / "data"


def ladder(pairs):
    return [{"strike": s, "mid": m} for s, m in pairs]


class ImpliedMean(unittest.TestCase):
    def test_a_short_or_empty_ladder_is_unreadable(self):
        self.assertIsNone(kalshi.implied_mean([]))
        self.assertIsNone(kalshi.implied_mean(ladder([(50, 0.5)])))

    def test_a_rising_survival_curve_is_refused(self):
        """P(X>=x) cannot increase with x. Reordering would invent coherence."""
        self.assertIsNone(kalshi.implied_mean(ladder([(10, 0.2), (20, 0.9)])))

    def test_a_flat_top_does_not_produce_an_infinite_tail(self):
        """
        Jerry Jeudy's Week 2 receiving ladder quoted 0.055 at both 80 and 90
        yards. In floating point the first was 0.05500000000000001, so the decay
        rate came out at 2.2e-18 and the tail integral at 2.5e15 yards -- the
        pooled MAE read 732 billion.
        """
        m = kalshi.implied_mean(ladder([
            (15, 0.70), (25, 0.545), (40, 0.315), (50, 0.215),
            (60, 0.14), (70, 0.095), (80, 0.05500000000000001), (90, 0.055)]))
        self.assertIsNotNone(m)
        mean, _ = m
        self.assertLess(mean, 200, "a receiving ladder topping out at 90 yards implies < 200")
        self.assertGreater(mean, 20)

    def test_the_mean_rises_with_the_market(self):
        low = kalshi.implied_mean(ladder([(20, 0.4), (40, 0.2), (60, 0.05)]))[0]
        high = kalshi.implied_mean(ladder([(20, 0.9), (40, 0.7), (60, 0.4)]))[0]
        self.assertGreater(high, low)

    def test_floor_span_reports_the_assumed_segment(self):
        _, span = kalshi.implied_mean(ladder([(100, 0.9), (110, 0.85), (120, 0.8)]))
        self.assertGreater(span, 0.5, "most of the mass is below a first strike of 100")


class PlayerRows(unittest.TestCase):
    def test_no_props_is_empty_not_an_error(self):
        self.assertEqual(kalshi.player_rows(None), {})
        self.assertEqual(kalshi.player_rows({}), {})

    def test_scrimmage_yards_does_not_double_count(self):
        props = {"series": {
            "A": {"stat": "rush_yards", "dkPointsPerUnit": 0.1, "players": [
                {"player": "X", "rungs": ladder([(20, 0.6), (40, 0.3), (60, 0.1)])}]},
            "B": {"stat": "scrimmage_yards", "dkPointsPerUnit": 0.1, "players": [
                {"player": "X", "rungs": ladder([(40, 0.6), (80, 0.3), (120, 0.1)])}]}}}
        row = kalshi.player_rows(props)["X"]
        self.assertIn("rush_yards", row["covered"])
        self.assertNotIn("scrimmage_yards", row["covered"])

    def test_scrimmage_is_used_when_nothing_else_is_there(self):
        props = {"series": {"B": {"stat": "scrimmage_yards", "dkPointsPerUnit": 0.1, "players": [
            {"player": "X", "rungs": ladder([(40, 0.6), (80, 0.3), (120, 0.1)])}]}}}
        self.assertEqual(kalshi.player_rows(props)["X"]["covered"], ["scrimmage_yards"])

    def test_missing_touchdown_markets_are_reported(self):
        props = {"series": {"A": {"stat": "rec_yards", "dkPointsPerUnit": 0.1, "players": [
            {"player": "X", "rungs": ladder([(20, 0.6), (40, 0.3), (60, 0.1)])}]}}}
        self.assertEqual(kalshi.player_rows(props)["X"]["missingScoring"], ["any_td", "pass_td"])

    def test_the_live_file_is_readable_and_sane(self):
        f = DATA / "kalshi-props.json"
        if not f.exists():
            self.skipTest("no kalshi file shipped")
        rows = kalshi.player_rows(json.loads(f.read_text()))
        self.assertTrue(rows)
        for name, r in rows.items():
            self.assertLess(r["points"], 60, f"{name}: implausible implied DK points")
            self.assertGreaterEqual(r["points"], 0)


class JoinToPool(unittest.TestCase):
    def test_the_pool_numbers_are_never_modified(self):
        """Kalshi is shown, not applied. If this ever fails, read the docstring."""
        pool = [{"name": "X", "projection": 12.0, "ceiling": 20.0, "salary": 5000}]
        props = {"series": {"A": {"stat": "rec_yards", "dkPointsPerUnit": 0.1, "players": [
            {"player": "X", "rungs": ladder([(20, 0.6), (40, 0.3), (60, 0.1)])}]}}}
        out = kalshi.join_to_pool(pool, props, lambda n: n)[0]
        self.assertEqual(out["projection"], 12.0)
        self.assertEqual(out["ceiling"], 20.0)
        self.assertEqual(out["salary"], 5000)
        self.assertIsNotNone(out["kalshi_points"])

    def test_a_player_with_no_market_gets_none_not_zero(self):
        pool = [{"name": "Nobody", "projection": 9.0}]
        out = kalshi.join_to_pool(pool, {"series": {}}, lambda n: n)[0]
        self.assertIsNone(out["kalshi_points"])


if __name__ == "__main__":
    unittest.main()

"""
The ownership model port.

These pin the invariants that matter for a ported model, not the numbers for
one slate: that every position's predicted shares still sum to its roster
slots, that nobody is dropped, and that a player with no usage record still
gets an estimate. See fv/ownership.py for what this build's inputs are a true
match for and what they are an approximation of.

Run with:  python3 -m unittest test_ownership -v
"""
import json, unittest
from pathlib import Path

from fv import ownership as own
from fv.pool import load_salaries, keep_starting_quarterbacks, rosterable

DATA = Path(__file__).parent / "data"


def _usage():
    return json.loads((DATA / "usage-2026.json").read_text())


def _pool():
    rows = load_salaries((DATA / "DKSalaries.csv").read_text())
    return [r for r in keep_starting_quarterbacks(rows) if rosterable(r)]


class Attach(unittest.TestCase):
    def test_every_player_gets_an_estimate(self):
        pool = _pool()
        out = own.attach(pool, _usage())
        self.assertEqual(len(out), len(pool))
        for r in out:
            self.assertIn("ownership", r)
            self.assertIsInstance(r["ownership"], float)

    def test_estimates_are_nonnegative_and_capped(self):
        for r in own.attach(_pool(), _usage()):
            self.assertGreaterEqual(r["ownership"], 0)
            self.assertLessEqual(r["ownership"], 100)

    def test_a_player_missing_from_usage_still_gets_an_estimate(self):
        pool = _pool()
        out = own.attach(pool, usage=None)
        self.assertEqual(len(out), len(pool))
        self.assertTrue(any(r["ownership"] > 0 for r in out))

    def test_position_totals_match_roster_slots(self):
        # The whole point of the renormalisation: whatever the fitted scores
        # say, a position's predictions sum to its roster slots, not to some
        # free-floating total. DST has no fitted coefficients and still has
        # to clear this bar via the flat fallback.
        pool = _pool()
        out = own.attach(pool, _usage())
        by_pos: dict[str, float] = {}
        for r in out:
            by_pos[r["position"]] = by_pos.get(r["position"], 0) + r["ownership"]
        for pos, total in by_pos.items():
            self.assertAlmostEqual(total, own.ROSTER_SLOTS.get(pos, 1) * 100, delta=0.5, msg=pos)

    def test_dst_gets_a_flat_undifferentiated_share(self):
        # No position was fitted for DST (the training workbook has no DST
        # rows), so every defence on the slate should come out identical
        # rather than silently differentiated by some other feature.
        pool = _pool()
        out = own.attach(pool, _usage())
        dsts = [r["ownership"] for r in out if r["position"] == "DST"]
        if len(dsts) > 1:
            self.assertAlmostEqual(max(dsts), min(dsts), places=6)

    def test_more_expensive_rb_outranks_cheaper_rb_all_else_equal(self):
        # Spot-check the sign, not the magnitude: salary is the single largest
        # coefficient at every position, so an otherwise-blank RB priced
        # higher should never come out less owned than one priced lower.
        model = own.load_model()
        cheap = {"position": "RB", "salary": 4000, "salaryPctileInPos": 0,
                 "valuePerK": 0, "trailingPts": 0, "trailingTgts": 0,
                 "trailingTouches": 0, "trailingSnap": 0, "home": False, "dome": False}
        rich = {**cheap, "salary": 9000}
        self.assertGreater(own._score(model, rich), own._score(model, cheap))


if __name__ == "__main__":
    unittest.main()

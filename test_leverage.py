"""
The leverage dial: discount a heavily-owned player's sampling weight so the
portfolio skews toward differentiated rosters, without ever excluding a
chalk play outright or trading away real projection for it.

Run with:  python3 -m unittest test_leverage -v
"""
import statistics
import unittest
from pathlib import Path

from fv.pool import load_salaries, keep_starting_quarterbacks, apply_ceilings, rosterable, load_json
from fv.slate import slate_options, main_slate, restrict
from fv.optimize import build_portfolio, improve_portfolio, projection
from fv.roster import is_stacked
from fv import ownership as own

DATA = Path(__file__).resolve().parent / "data"


def live_pool():
    rows = load_salaries((DATA / "DKSalaries.csv").read_text())
    rows = [r for r in keep_starting_quarterbacks(rows) if rosterable(r)]
    rows = apply_ceilings(rows, load_json(DATA / "player-variance.json"))
    slate = main_slate(slate_options(rows))
    kept, _ = restrict(rows, slate)
    usage = load_json(DATA / "usage-2026.json")
    return own.attach(kept, usage)


def avg_ownership(lineups: list[list[dict]]) -> float:
    pts = [p.get("ownership", 0) for l in lineups for p in l]
    return sum(pts) / len(pts) if pts else 0.0


class Default(unittest.TestCase):
    """leverage_weight defaults to 0 and must change nothing already measured."""

    def test_zero_leverage_matches_omitting_the_argument(self):
        pool = live_pool()
        a = build_portfolio(pool, 5, seed=1, attempts=6000)
        b = build_portfolio(pool, 5, seed=1, attempts=6000, leverage_weight=0.0)
        self.assertEqual([sorted(p["id"] for p in l) for l in a],
                         [sorted(p["id"] for p in l) for l in b])

    def test_zero_leverage_improve_matches_omitting_the_argument(self):
        pool = live_pool()
        built = build_portfolio(pool, 3, seed=2, attempts=6000)
        a = improve_portfolio(built, pool, rounds=3)
        b = improve_portfolio(built, pool, rounds=3, leverage_weight=0.0)
        self.assertEqual([sorted(p["id"] for p in l) for l in a],
                         [sorted(p["id"] for p in l) for l in b])


class Leverage(unittest.TestCase):
    def test_higher_leverage_lowers_average_ownership(self):
        # Directional, not a pinned number -- next week's slate will not
        # reproduce today's figures. A single seed is noisy at only 10
        # lineups, so this averages across several, which is the same check
        # that confirmed the mechanism works before this test was written.
        pool = live_pool()
        low_avg = statistics.mean(
            avg_ownership(build_portfolio(pool, 10, seed=s, attempts=8000, leverage_weight=0.0))
            for s in range(1, 6))
        high_avg = statistics.mean(
            avg_ownership(build_portfolio(pool, 10, seed=s, attempts=8000, leverage_weight=1.0))
            for s in range(1, 6))
        self.assertLess(high_avg, low_avg)

    def test_leveraged_lineups_are_still_legal(self):
        pool = live_pool()
        built = build_portfolio(pool, 5, seed=6, attempts=8000, leverage_weight=0.8)
        improved = improve_portfolio(built, pool, rounds=3, leverage_weight=0.8)
        self.assertEqual(len(improved), 5)
        for l in improved:
            self.assertEqual(len(l), 9)
            self.assertTrue(is_stacked(l))
            self.assertLessEqual(sum(p["salary"] for p in l), 50000)

    def test_improve_does_not_trade_projection_for_leverage(self):
        # The hill-climb's whole job is raising the score it is handed. With
        # leverage on, that score already prices in ownership, so a completed
        # pass must never have made the RAW projection worse -- the discount
        # changes which swap looks best, not whether the lineup is good.
        pool = live_pool()
        built = build_portfolio(pool, 5, seed=7, attempts=8000, leverage_weight=0.5)
        before = [projection(l) for l in built]
        after_proj = [projection(l) for l in improve_portfolio(
            [list(l) for l in built], pool, rounds=3, leverage_weight=0.5)]
        for b, a in zip(before, after_proj):
            self.assertGreaterEqual(a, b - 1e-9)

    def test_a_pool_built_without_ownership_still_builds(self):
        # fv.ownership.attach() is one extra step a caller could forget (or a
        # pool built before the usage file exists this early in a season).
        # Missing ownership must read as 0%, not crash the sampler.
        pool = load_salaries((DATA / "DKSalaries.csv").read_text())
        pool = [r for r in keep_starting_quarterbacks(pool) if rosterable(r)]
        pool = apply_ceilings(pool, load_json(DATA / "player-variance.json"))
        slate = main_slate(slate_options(pool))
        pool, _ = restrict(pool, slate)
        self.assertNotIn("ownership", pool[0])
        lineups = build_portfolio(pool, 1, seed=8, attempts=4000, leverage_weight=1.0)
        self.assertEqual(len(lineups), 1)


if __name__ == "__main__":
    unittest.main()

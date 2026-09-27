"""
Avoiding a partner's players.

Run with:  python3 -m unittest test_partner -v
"""
import json, unittest
from pathlib import Path

from fv import partner

DATA = Path(__file__).parent / "data"


class Load(unittest.TestCase):
    def test_a_missing_file_switches_the_feature_off(self):
        d = partner.load(DATA / "no-such-partner-file.json")
        self.assertEqual(d["players"], [])

    def test_a_bare_list_is_accepted(self):
        p = DATA / "_tmp_partner.json"
        p.write_text(json.dumps(["Alpha One", "Beta Two"]))
        try:
            self.assertEqual(partner.load(p)["players"], ["Alpha One", "Beta Two"])
        finally:
            p.unlink()


class Exclude(unittest.TestCase):
    POOL = [{"name": "Alpha One"}, {"name": "Beta Two"}, {"name": "Gamma Three"}]

    def test_nothing_to_avoid_leaves_the_pool_alone(self):
        out, missing = partner.exclude(self.POOL, {"players": []})
        self.assertEqual(len(out), 3)
        self.assertEqual(missing, [])
        out, _ = partner.exclude(self.POOL, None)
        self.assertEqual(len(out), 3)

    def test_it_removes_the_partner_players(self):
        out, _ = partner.exclude(self.POOL, {"players": ["Beta Two"]})
        self.assertEqual([p["name"] for p in out], ["Alpha One", "Gamma Three"])

    def test_a_name_that_does_not_match_is_REPORTED(self):
        """
        A silent miss is the failure mode: the player stays available and
        quietly recreates the overlap the feature exists to prevent.
        """
        out, missing = partner.exclude(self.POOL, {"players": ["Beta Two", "Nobody Here"]})
        self.assertEqual(missing, ["Nobody Here"])
        self.assertEqual(len(out), 2)

    def test_the_live_file_if_present_mostly_matches_the_slate(self):
        f = DATA / "partner-players.json"
        if not f.exists():
            self.skipTest("no partner file locally")
        from fv.pool import load_salaries
        pool = load_salaries((DATA / "DKSalaries.csv").read_text())
        d = partner.load(f)
        _, missing = partner.exclude(pool, d)
        self.assertLess(len(missing), len(d["players"]) / 2,
                        f"over half the partner's names do not match the slate: {missing}")

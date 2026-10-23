"""
fv.injuries.role_opportunity: naming a mechanism, not scoring one.

Run with:  python3 -m unittest test_opportunity -v
"""
import unittest

from fv import injuries


def feed_entry(name, team, position, status, body_part=""):
    return {"name": name, "status": status, "body_part": body_part, "team": team, "position": position}


class RoleOpportunity(unittest.TestCase):
    def test_a_blocked_teammate_at_the_same_position_is_flagged(self):
        players = [{"id": "p1", "name": "Backup Back", "team": "HOU", "position": "RB"}]
        feed = {"starter": feed_entry("Starting Back", "HOU", "RB", "Doubtful", "groin")}
        out = injuries.role_opportunity(players, feed)
        self.assertIn("p1", out)
        self.assertEqual(out["p1"][0]["name"], "Starting Back")
        self.assertEqual(out["p1"][0]["status"], "Doubtful")

    def test_a_different_position_does_not_count(self):
        # A hurt tight end does not obviously hand snaps to a receiver.
        players = [{"id": "p1", "name": "Some WR", "team": "HOU", "position": "WR"}]
        feed = {"te": feed_entry("Hurt TE", "HOU", "TE", "Out")}
        out = injuries.role_opportunity(players, feed)
        self.assertNotIn("p1", out)

    def test_a_different_team_does_not_count(self):
        players = [{"id": "p1", "name": "Some RB", "team": "MIA", "position": "RB"}]
        feed = {"rb": feed_entry("Hurt RB", "HOU", "RB", "Out")}
        out = injuries.role_opportunity(players, feed)
        self.assertNotIn("p1", out)

    def test_a_healthy_teammate_does_not_count(self):
        players = [{"id": "p1", "name": "Some RB", "team": "HOU", "position": "RB"}]
        feed = {"rb": feed_entry("Healthy RB", "HOU", "RB", "Questionable")}
        out = injuries.role_opportunity(players, feed)
        self.assertNotIn("p1", out)

    def test_a_player_is_not_his_own_blocking_teammate(self):
        # A player who is himself hurt but still barely rosterable (e.g. a
        # file that hasn't caught up) must not show up as his own opportunity.
        players = [{"id": "p1", "name": "Tweener Back", "team": "HOU", "position": "RB"}]
        feed = {"same": feed_entry("Tweener Back", "HOU", "RB", "Doubtful")}
        out = injuries.role_opportunity(players, feed)
        self.assertNotIn("p1", out)

    def test_multiple_blocking_teammates_are_all_listed(self):
        players = [{"id": "p1", "name": "Third Receiver", "team": "SF", "position": "WR"}]
        feed = {
            "a": feed_entry("First Receiver", "SF", "WR", "Out"),
            "b": feed_entry("Second Receiver", "SF", "WR", "IR"),
        }
        out = injuries.role_opportunity(players, feed)
        self.assertEqual({h["name"] for h in out["p1"]}, {"First Receiver", "Second Receiver"})


if __name__ == "__main__":
    unittest.main()

"""End-to-end: scanner -> entry -> exit -> budget -> risk -> ticket, offline."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from _load import load, ensure_fixtures_env

ensure_fixtures_env()


class PipelineTests(unittest.TestCase):

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        # The skills write into ./psx/*; isolate per-test.
        # Re-import each skill cleanly because they share module state (psx_http).
        self.scan = load("psx-scanner", "scan")
        self.entry = load("psx-entry", "entry")
        self.exit_ = load("psx-exit", "exit")
        self.budget = load("psx-budget", "size")
        self.risk = load("psx-risk", "check")
        self.ticket = load("psx-ticket", "ticket")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_pipeline_hubc_long_orb(self):
        # scanner daily
        rc = self.scan.main(["daily", "--symbols", "HUBC",
                             "--now", "2026-11-17 09:00"])
        self.assertEqual(rc, 0)

        # entry
        rc = self.entry.main([
            "HUBC", "--rule", "orb", "--long",
            "--at", "15:00", "--now", "2026-11-16 15:00"])
        self.assertEqual(rc, 0)
        with open("psx/entry.json") as fh:
            e = json.load(fh)
        self.assertEqual(e["symbol"], "HUBC")
        self.assertEqual(e["side"], "long")
        self.assertEqual(e["trigger_price"], 208.0)

        # exit: or-low, RR=2
        rc = self.exit_.main(["--stop", "or-low", "--rr", "2"])
        self.assertEqual(rc, 0)
        with open("psx/plan.json") as fh:
            plan = json.load(fh)
        self.assertEqual(plan["stop"], 204.3)
        self.assertEqual(plan["target"], 215.4)

        # budget
        rc = self.budget.main([])
        self.assertEqual(rc, 0)
        with open("psx/order.json") as fh:
            order = json.load(fh)
        # Position-pct cap: 10% of 500k = 50k / entry 208 = 240 shares
        self.assertEqual(order["qty"], 240)
        self.assertEqual(order["limit_hit"], "max_position_pct")
        # Net R/R is lower than gross 2.0 because of costs + CGT
        self.assertLess(order["net_rr"], 2.0)
        self.assertGreater(order["net_rr"], 0.5)

        # risk (shariah off so HUBC doesn't warn)
        rc = self.risk.main([
            "--prev-close", "205.74",
            "--now", "2026-11-17 10:00",
        ])
        self.assertEqual(rc, 0)
        with open("psx/state/risk.json") as fh:
            r = json.load(fh)
        self.assertIn(r["verdict"], ("PASS", "WARN"))
        # fingerprint must be stable from risk to ticket
        self.fp = r["fingerprint"]

        # ticket: refuse without --approve
        rc = self.ticket.main(["write"])
        self.assertEqual(rc, 1)

        # ticket: accept only exact "yes"
        for bad in ("y", "YES BUT", "ok", "all"):
            rc = self.ticket.main(["write", "--approve", bad])
            self.assertEqual(rc, 1, f"ticket should refuse {bad!r}")

        # ticket: write
        rc = self.ticket.main(["write", "--approve", "yes"])
        self.assertEqual(rc, 0)
        with open("psx/state/broker.json") as fh:
            b = json.load(fh)
        self.assertEqual(len(b["orders"]), 1)
        self.assertEqual(b["orders"][0]["fingerprint"], self.fp)
        self.assertEqual(b["orders"][0]["status"], "pending")

        # fill
        rc = self.ticket.main([
            "fill", self.fp, "--price", "208.25", "--qty", "240"])
        self.assertEqual(rc, 0)
        with open("psx/state/broker.json") as fh:
            b = json.load(fh)
        self.assertEqual(b["orders"][0]["status"], "filled")
        self.assertEqual(len(b["fills"]), 1)
        self.assertEqual(b["fills"][0]["price"], 208.25)


if __name__ == "__main__":
    unittest.main()

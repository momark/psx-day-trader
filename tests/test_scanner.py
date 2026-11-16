"""Scanner: universe + daily + ORB, all against the captured fixtures."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from _load import load, ensure_fixtures_env, ROOT

ensure_fixtures_env()
scan = load("psx-scanner", "scan")


class ScannerTests(unittest.TestCase):

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_universe_kse100_has_100_symbols(self):
        rc = scan.main(["universe", "--index", "KSE100"])
        self.assertEqual(rc, 0)
        with open("psx/universe.txt") as fh:
            syms = [line.strip() for line in fh if line.strip()]
        self.assertEqual(len(syms), 100)
        self.assertIn("OGDC", syms)
        self.assertIn("HUBC", syms)
        self.assertIn("LUCK", syms)

    def test_universe_kmi30_has_30_symbols(self):
        rc = scan.main(["universe", "--index", "KMI30"])
        self.assertEqual(rc, 0)
        with open("psx/universe.txt") as fh:
            syms = [line.strip() for line in fh if line.strip()]
        self.assertEqual(len(syms), 30)

    def test_universe_shariah_alias(self):
        rc = scan.main(["universe", "--shariah"])
        self.assertEqual(rc, 0)
        with open("psx/universe.txt") as fh:
            syms = [line.strip() for line in fh if line.strip()]
        # KMIALLSHR has ~300 Shariah-compliant symbols, way more than KMI-30
        self.assertGreater(len(syms), 100)

    def test_daily_finds_setups_on_fixtures(self):
        rc = scan.main([
            "daily", "--symbols", "OGDC,HUBC,LUCK,ENGROH", "--now", "2026-11-17 09:00",
        ])
        self.assertEqual(rc, 0)
        with open("psx/watchlist.json") as fh:
            obj = json.load(fh)
        self.assertEqual(obj["scope"], "daily")
        rules = {s["rule"] for s in obj["setups"]}
        # The 4 known symbols are expected to trigger at least 1 setup overall
        self.assertGreater(len(obj["setups"]), 0)
        for s in obj["setups"]:
            self.assertIn(s["symbol"], {"OGDC", "HUBC", "LUCK", "ENGROH"})

    def test_rsi14(self):
        self.assertIsNone(scan.rsi14([1.0]))
        # 15 flat prices -> RSI is undefined (no moves); our impl returns 100
        rsi = scan.rsi14([100.0] * 20)
        self.assertEqual(rsi, 100.0)

    def test_ticks_to_5min_bars_bucketing(self):
        # Three ticks in the 09:30-09:35 PKT bucket; two in the 09:35-09:40 bucket.
        # Timestamps in PKT = UTC+5.  Use a known Monday in Nov 2026.
        import datetime as dt
        from zoneinfo import ZoneInfo
        pkt = ZoneInfo("Asia/Karachi")
        base = dt.datetime(2026, 11, 16, 9, 32, tzinfo=pkt)
        ticks = []
        for i, (dm, p, v) in enumerate([
            (0, 100.0, 50),      # 09:32:00 -> bucket 09:30
            (1.5, 101.0, 25),    # 09:33:30
            (4.0, 102.0, 100),   # 09:36:00 -> bucket 09:35
            (6.5, 103.0, 10),    # 09:38:30
        ]):
            ts = int((base + dt.timedelta(minutes=dm)).timestamp())
            ticks.append([ts, p, v])
        bars = scan.ticks_to_5min_bars(ticks)
        # NOTE: 09:32 floors to 09:30 and 09:36 floors to 09:35
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["v"], 75)
        self.assertEqual(bars[1]["v"], 110)


if __name__ == "__main__":
    unittest.main()

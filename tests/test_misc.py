"""Tests for the smaller skills: news, timemachine, alerts, analyst, fingerprint."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from _load import load, ensure_fixtures_env

ensure_fixtures_env()


class NewsTests(unittest.TestCase):

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        self.news = load("psx-news", "news")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_classifies(self):
        self.assertEqual(
            self.news._classify("Material Information - Issue of TFC"),
            "material")
        self.assertEqual(
            self.news._classify("Interim Dividend 25%"),
            "dividend")
        self.assertEqual(
            self.news._classify("Board Meeting Notice"),
            "meeting")
        self.assertEqual(
            self.news._classify("Quarterly Financial Statements"),
            "financial_result")
        self.assertEqual(
            self.news._classify("Submission of Shariah Compliant Accounts"),
            "shariah")

    def test_runs_against_fixtures(self):
        rc = self.news.main(["--symbols", "ENGROH"])
        self.assertEqual(rc, 0)
        with open("psx/state/news.json") as fh:
            obj = json.load(fh)
        self.assertGreater(len(obj["items"]), 0)


class TimemachineTests(unittest.TestCase):

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        self.tm = load("psx-timemachine", "timemachine")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verdict_bands(self):
        self.assertEqual(self.tm.verdict(55, 10, 35), "STRONG_TARGET")
        self.assertEqual(self.tm.verdict(10, 55, 35), "STRONG_STOP")
        self.assertEqual(self.tm.verdict(30, 29, 41), "MANY_NEITHER")
        self.assertEqual(self.tm.verdict(30, 32, 38), "COIN_FLIP")
        self.assertEqual(self.tm.verdict(35, 25, 40), "MIXED")  # diff=10 < STRONG=15

    def test_runs_with_seed(self):
        rc = self.tm.main([
            "--symbol", "OGDC", "--entry", "316", "--stop", "311",
            "--target", "326", "--horizon-days", "3", "--draws", "500",
            "--seed", "19"])
        self.assertEqual(rc, 0)
        with open("psx/timemachine.json") as fh:
            out = json.load(fh)
        self.assertEqual(out["symbol"], "OGDC")
        self.assertIn(out["verdict"],
                      {"STRONG_TARGET", "STRONG_STOP",
                       "COIN_FLIP", "MANY_NEITHER", "MIXED"})
        total = out["target_pct"] + out["stop_pct"] + out["neither_pct"]
        self.assertAlmostEqual(total, 100.0, delta=0.05)


class AlertsTests(unittest.TestCase):

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        self.alerts = load("psx-alerts", "alerts")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_then_list(self):
        self.alerts.main(["add", "OGDC", "above", "320"])
        self.alerts.main(["add", "HUBC", "below", "204.3"])
        with open("psx/state/alerts.json") as fh:
            obj = json.load(fh)
        self.assertEqual(len(obj["alerts"]), 2)

    def test_check_fires_below(self):
        self.alerts.main(["add", "OGDC", "above", "1"])
        # tick for OGDC's latest is ~316.2 which is way above 1
        rc = self.alerts.main(["check"])
        self.assertEqual(rc, 0)
        with open("psx/state/alerts.json") as fh:
            obj = json.load(fh)
        self.assertEqual(obj["alerts"][0]["status"], "triggered")


class AnalystTests(unittest.TestCase):

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        self.facts = load("psx-analyst", "facts")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reports_listed_for_ogdc(self):
        rc = self.facts.main(["OGDC"])
        self.assertEqual(rc, 0)
        with open("psx/analyst/OGDC.json") as fh:
            obj = json.load(fh)
        self.assertEqual(obj["symbol"], "OGDC")
        self.assertGreater(len(obj["reports"]), 0)
        self.assertIn("pdf", obj["reports"][0])


class FingerprintTests(unittest.TestCase):

    def test_stable_and_order_sensitive(self):
        from psx_fingerprint import fingerprint
        a = {"symbol": "HUBC", "side": "long", "qty": 240,
             "entry": 208.0, "stop": 204.3, "target": 215.4}
        b = dict(a)
        self.assertEqual(fingerprint(a), fingerprint(b))
        # mutate any one field -> fingerprint must change
        b["qty"] = 241
        self.assertNotEqual(fingerprint(a), fingerprint(b))


if __name__ == "__main__":
    unittest.main()

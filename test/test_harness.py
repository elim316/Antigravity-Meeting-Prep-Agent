#!/usr/bin/env python3
"""Unit and integration test suite for the Meeting Prep Agent harness (10 suites)."""

import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

TEST_DIR = pathlib.Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import build_test_prompt
import check_output
import mock_tool

FIXED_NOW = "2026-09-23T17:00:00+08:00"


def _run_mock(scenario, outbox, *args):
    env = {**os.environ, "MP_SCENARIO": scenario, "MP_OUTBOX": str(outbox), "MP_NOW": FIXED_NOW}
    return subprocess.run(
        [sys.executable, str(TEST_DIR / "mock_tool.py"), *args],
        capture_output=True, text=True, env=env, check=True,
    ).stdout


class TestMeetingPrepHarness(unittest.TestCase):
    def test_next_business_day_weekday_and_weekend(self):
        for day, expected in [((2026, 10, 1), (2026, 10, 2)), ((2026, 10, 2), (2026, 10, 5)), ((2026, 10, 3), (2026, 10, 5))]:
            self.assertEqual(mock_tool.next_business_day(datetime.date(*day)), datetime.date(*expected))

    def test_flag_and_positional_parsing(self):
        args = ["readonly", "search", "--max", "10", "--format", "json", "--query=QBR", "--query", "Sync", "Acme"]
        self.assertEqual(mock_tool.get_flag(args, "--max"), "10")
        self.assertIsNone(mock_tool.get_flag(args, "--missing"))
        self.assertEqual(mock_tool.get_all_flags(args, "--query"), ["QBR", "Sync"])
        self.assertEqual(mock_tool.positional_args(args, ("readonly", "search")), ["Acme"])

    def test_resolve_when_variants(self):
        tz = ZoneInfo("Asia/Singapore")
        ref = datetime.datetime(2026, 10, 2, 17, 0, tzinfo=tz)
        s, e, ad = mock_tool.resolve_when({"kind": "next_business_day", "time": "10:30", "duration_min": 45}, ref)
        self.assertEqual((s, e, ad), (datetime.datetime(2026, 10, 5, 10, 30, tzinfo=tz), datetime.datetime(2026, 10, 5, 11, 15, tzinfo=tz), False))
        self.assertTrue(mock_tool.resolve_when({"kind": "today", "all_day": True}, ref)[2])
        self.assertEqual(mock_tool.resolve_when({"kind": "offset_minutes", "value": 75, "duration_min": 30}, ref)[0], datetime.datetime(2026, 10, 2, 18, 15, tzinfo=tz))

    def test_gcalendar_edge_cases(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            evs = json.loads(_run_mock("next_day_mixed", tmp, "gcalendar", "readonly", "events", "--date", "2026-09-24", "--max", "2"))
            self.assertEqual(len(evs), 2)
            hits = json.loads(_run_mock("next_day_mixed", tmp, "gcalendar", "readonly", "search", "--max", "10", "Acme"))
            self.assertEqual(len(hits), 1)
            self.assertIn("error", json.loads(_run_mock("next_day_mixed", tmp, "gcalendar", "readonly", "get", "missing-id")))
            self.assertEqual(json.loads(_run_mock("next_day_mixed", tmp, "gcalendar", "workloc"))["timezone"], "Asia/Singapore")

    def test_people_known_unknown_and_full_dump(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            res = json.loads(_run_mock("next_day_mixed", tmp, "people", "alex.chen@acme.example.com", "nobody@acme.example.com"))
            self.assertEqual(res["alex.chen@acme.example.com"]["name"], "Alex Chen")
            self.assertIn("NOT FOUND", res["nobody@acme.example.com"])
            self.assertGreaterEqual(len(json.loads(_run_mock("next_day_mixed", tmp, "people"))), 14)

    def test_csa_cli_best_match_and_fallback(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            self.assertIn("security review", _run_mock("next_day_mixed", tmp, "csa_cli", "--user_prompt=Acme Alex Chen rollout"))
            self.assertIn("No relevant results", _run_mock("next_day_mixed", tmp, "csa_cli", "--user_prompt=unrelated topic"))

    def test_gdrive_gdocs_and_gmail_edge_cases(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            md = pathlib.Path(tmp) / "brief.md"
            md.write_text("## Meeting Details\nAcme Sync\n", encoding="utf-8")
            out = _run_mock("next_day_mixed", tmp, "gdocs", "mutate", "import-md", "--title", "Meeting Prep Dossier - 2026-09-24 - Acme", str(md))
            doc_url = out.strip().splitlines()[-1]
            self.assertIn("Acme Sync", _run_mock("next_day_mixed", tmp, "gdocs", "readonly", "read", doc_url))
            self.assertIn("Q4 Roadmap", _run_mock("next_day_mixed", tmp, "gdrive", "readonly", "search", "--name-contains", "Roadmap"))
            _run_mock("next_day_mixed", tmp, "gmail", "mutate", "send-self", "--subject", "Digest", "--body", "Body", "--md")
            self.assertEqual(len(check_output.read_outbox(pathlib.Path(tmp))[0]), 1)

    def test_lint_hallucinations_catches_fabricated_entities(self):
        world, sc = check_output.load("next_day_mixed")
        corpus = check_output.corpus_for(world, sc)
        clean = "## Meeting Details\nAlex Morgan\nhttps://docs.google.com/document/d/mock-123/edit\nhttps://b.corp.google.com/issues/412998877"
        self.assertEqual(check_output.lint_hallucinations(clean, corpus), [])
        bad = check_output.lint_hallucinations("Met Zaphod Beeblebrox on b/999999999 at https://evil.example.org/x.", corpus)
        self.assertEqual({k for k, _ in bad}, {"name", "bug", "url"})

    def test_install_sh_and_live_sidecars(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            subprocess.run([str(TEST_DIR.parent / "install.sh"), "--email", "u@example.com", "--target", tmp], check=True, capture_output=True)
            for name in ("meeting-prep-dossier", "meeting-prep-reminder"):
                cfg = json.loads((pathlib.Path(tmp) / name / "sidecar.json").read_text(encoding="utf-8"))
                self.assertIn("mcp(*)", cfg["agent_permissions"]["access_grants"])
                self.assertIn("u@example.com", cfg["args"][-1])

    def test_all_five_scenarios_prompt_build_and_grading(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            for sc_name in mock_tool.world()["scenarios"]:
                res = subprocess.run([sys.executable, str(TEST_DIR / "build_test_prompt.py"), "--scenario", sc_name, "--outbox", tmp], capture_output=True, text=True, check=True)
                self.assertIn("alex.morgan@company.example.com", res.stdout)
                self.assertNotIn("__USER_EMAIL__", res.stdout)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unittest.mock import patch, MagicMock

from src.scrape import load_config, get_date_range, read_last_run, write_last_run, run_gh


class TestConfig(unittest.TestCase):
    def test_load_config_expands_tilde(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config = {
                "repos": ["owner/repo"],
                "users": ["owner"],
                "activity_types": ["prs"],
                "fields": {"prs": ["number"]},
                "cron_schedule": "0 * * * *",
                "data_dir": "~/test-data",
                "log_file": "~/test.log",
                "bootstrap_days": 1,
            }
            config_path.write_text(json.dumps(config))

            result = load_config(config_path)

            self.assertEqual(result["data_dir"], os.path.expanduser("~/test-data"))
            self.assertEqual(result["log_file"], os.path.expanduser("~/test.log"))


class TestDateRange(unittest.TestCase):
    def test_get_date_range_with_last_run(self):
        now = datetime.now(timezone.utc)
        last_run = now - timedelta(hours=3)
        since, until = get_date_range(last_run, bootstrap_days=7)
        self.assertEqual(since, last_run.isoformat())
        self.assertLess(datetime.fromisoformat(until) - now, timedelta(minutes=1))

    def test_get_date_range_without_last_run(self):
        now = datetime.now(timezone.utc)
        since, until = get_date_range(None, bootstrap_days=2)
        expected_since = (now - timedelta(days=2)).isoformat()
        self.assertLess(
            abs(datetime.fromisoformat(since) - datetime.fromisoformat(expected_since)),
            timedelta(minutes=1)
        )
        self.assertLess(datetime.fromisoformat(until) - now, timedelta(minutes=1))


class TestLastRun(unittest.TestCase):
    def test_read_last_run_returns_datetime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            last_run_path = Path(tmpdir) / "last_run.txt"
            now = datetime.now(timezone.utc).replace(microsecond=0)
            last_run_path.write_text(now.isoformat())

            result = read_last_run(last_run_path)
            self.assertEqual(result, now)

    def test_read_last_run_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            last_run_path = Path(tmpdir) / "last_run.txt"
            self.assertIsNone(read_last_run(last_run_path))

    def test_write_last_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            last_run_path = Path(tmpdir) / "last_run.txt"
            now = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
            write_last_run(last_run_path, now)
            self.assertEqual(last_run_path.read_text(), "2026-06-12T12:00:00+00:00")


class TestRunGh(unittest.TestCase):
    def test_run_gh_returns_json(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '[{"number": 1}]'
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = run_gh(["pr", "list", "--json", "number"])
            self.assertEqual(result, [{"number": 1}])
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            self.assertEqual(args[0], ["gh", "pr", "list", "--json", "number"])

    def test_run_gh_empty_stdout_returns_empty_list(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = run_gh(["api", "repos/owner/repo/issues/comments"])
            self.assertEqual(result, [])

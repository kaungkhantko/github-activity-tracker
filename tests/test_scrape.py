import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unittest.mock import patch, MagicMock

from src.scrape import (
    load_config, get_date_range, read_last_run, write_last_run, run_gh,
    fetch_prs, fetch_issues, fetch_comments, fetch_commits,
    split_by_day, write_daily_data,
)


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


class TestFetchPrs(unittest.TestCase):
    def test_fetch_prs_filters_fields(self):
        raw_prs = [
            {
                "number": 1,
                "title": "PR One",
                "state": "open",
                "url": "https://github.com/owner/repo/pull/1",
                "createdAt": "2026-06-12T10:00:00Z",
                "closedAt": None,
                "mergedAt": None,
                "author": {"login": "owner"},
                "labels": [{"name": "bug"}],
                "body": "Description",
                "extra_field": "should be removed"
            }
        ]
        fields = ["number", "title", "state", "url", "created_at", "closed_at", "merged_at", "author", "labels", "body"]
        result = fetch_prs(raw_prs, fields)
        self.assertEqual(result, [
            {
                "number": 1,
                "title": "PR One",
                "state": "open",
                "url": "https://github.com/owner/repo/pull/1",
                "created_at": "2026-06-12T10:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "author": "owner",
                "labels": ["bug"],
                "body": "Description"
            }
        ])


class TestFetchIssues(unittest.TestCase):
    def test_fetch_issues_filters_fields(self):
        raw = [
            {
                "number": 1,
                "title": "Issue One",
                "state": "closed",
                "url": "https://github.com/owner/repo/issues/1",
                "createdAt": "2026-06-12T10:00:00Z",
                "closedAt": "2026-06-12T11:00:00Z",
                "author": {"login": "owner"},
                "labels": [{"name": "bug"}],
                "body": "Bug description"
            }
        ]
        fields = ["number", "title", "state", "url", "created_at", "closed_at", "author", "labels", "body"]
        result = fetch_issues(raw, fields)
        self.assertEqual(result[0]["author"], "owner")
        self.assertEqual(result[0]["labels"], ["bug"])
        self.assertEqual(result[0]["state"], "closed")


class TestFetchComments(unittest.TestCase):
    def test_fetch_comments_filters_fields(self):
        raw = [
            {
                "issue": {"number": 1},
                "pullRequest": None,
                "body": "Comment",
                "url": "https://github.com/owner/repo/issues/1#issuecomment-1",
                "createdAt": "2026-06-12T10:00:00Z",
                "author": {"login": "owner"}
            }
        ]
        fields = ["type", "issue_number", "pr_number", "body", "url", "created_at", "author"]
        result = fetch_comments(raw, fields)
        self.assertEqual(result[0], {
            "type": "issue_comment",
            "issue_number": 1,
            "pr_number": None,
            "body": "Comment",
            "url": "https://github.com/owner/repo/issues/1#issuecomment-1",
            "created_at": "2026-06-12T10:00:00Z",
            "author": "owner"
        })


class TestSplitByDay(unittest.TestCase):
    def test_split_by_day_groups_items_by_day(self):
        items = [
            {"created_at": "2026-06-12T10:00:00Z"},
            {"created_at": "2026-06-13T11:00:00Z"},
            {"date": "2026-06-12T15:00:00Z"},
        ]
        result = split_by_day(items)
        self.assertIn("2026-06-12", result)
        self.assertIn("2026-06-13", result)
        self.assertEqual(len(result["2026-06-12"]), 2)
        self.assertEqual(len(result["2026-06-13"]), 1)


class TestWriteDailyData(unittest.TestCase):
    def test_write_daily_data_appends_to_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            day = "2026-06-12"
            existing = {
                "date": day,
                "repos": {}
            }
            (data_dir / f"{day}.json").write_text(json.dumps(existing))

            new_data = {
                "owner/repo-1": {
                    "users": {
                        "kaungkhantko": {
                            "prs": [{"number": 1, "title": "PR One"}]
                        }
                    }
                }
            }

            write_daily_data(data_dir, day, new_data)

            updated = json.loads((data_dir / f"{day}.json").read_text())
            self.assertEqual(
                updated["repos"]["owner/repo-1"]["users"]["kaungkhantko"]["prs"],
                [{"number": 1, "title": "PR One"}]
            )

    def test_write_daily_data_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            day = "2026-06-12"
            existing = {
                "date": day,
                "repos": {
                    "owner/repo-1": {
                        "users": {
                            "kaungkhantko": {
                                "prs": [{"number": 1, "title": "PR One"}]
                            }
                        }
                    }
                }
            }
            (data_dir / f"{day}.json").write_text(json.dumps(existing))

            new_data = {
                "owner/repo-1": {
                    "users": {
                        "kaungkhantko": {
                            "prs": [{"number": 1, "title": "PR One Duplicate"}]
                        }
                    }
                }
            }

            write_daily_data(data_dir, day, new_data)

            updated = json.loads((data_dir / f"{day}.json").read_text())
            self.assertEqual(
                len(updated["repos"]["owner/repo-1"]["users"]["kaungkhantko"]["prs"]),
                1
            )


class TestFetchCommits(unittest.TestCase):
    def test_fetch_commits_filters_fields(self):
        raw = [
            {
                "sha": "abc123",
                "commit": {"message": "Fix bug", "committer": {"date": "2026-06-12T10:00:00Z"}},
                "html_url": "https://github.com/owner/repo/commit/abc123",
                "author": {"login": "owner"},
                "files": [{"filename": "src/main.py"}]
            }
        ]
        fields = ["sha", "message", "url", "date", "author", "files_changed"]
        result = fetch_commits(raw, fields)
        self.assertEqual(result[0], {
            "sha": "abc123",
            "message": "Fix bug",
            "url": "https://github.com/owner/repo/commit/abc123",
            "date": "2026-06-12T10:00:00Z",
            "author": "owner",
            "files_changed": ["src/main.py"]
        })

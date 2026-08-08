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
    def test_load_config_expands_tilde(self) -> None:
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

    def test_load_config_does_not_mutate_input(self) -> None:
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
            original = json.loads(config_path.read_text())

            load_config(config_path)

            after = json.loads(config_path.read_text())
            self.assertEqual(after, original)


class TestDateRange(unittest.TestCase):
    def test_get_date_range_with_last_run(self) -> None:
        now = datetime.now(timezone.utc)
        last_run = now - timedelta(hours=3)
        since, until = get_date_range(last_run, bootstrap_days=7)
        self.assertEqual(since, last_run.isoformat())
        self.assertLess(datetime.fromisoformat(until) - now, timedelta(minutes=1))

    def test_get_date_range_without_last_run(self) -> None:
        now = datetime.now(timezone.utc)
        since, until = get_date_range(None, bootstrap_days=2)
        expected_since = (now - timedelta(days=2)).isoformat()
        self.assertLess(
            abs(datetime.fromisoformat(since) - datetime.fromisoformat(expected_since)),
            timedelta(minutes=1)
        )
        self.assertLess(datetime.fromisoformat(until) - now, timedelta(minutes=1))


class TestLastRun(unittest.TestCase):
    def test_read_last_run_returns_datetime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            last_run_path = Path(tmpdir) / "last_run.txt"
            now = datetime.now(timezone.utc).replace(microsecond=0)
            last_run_path.write_text(now.isoformat())

            result = read_last_run(last_run_path)
            self.assertEqual(result, now)

    def test_read_last_run_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            last_run_path = Path(tmpdir) / "last_run.txt"
            self.assertIsNone(read_last_run(last_run_path))

    def test_write_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            last_run_path = Path(tmpdir) / "last_run.txt"
            now = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
            write_last_run(last_run_path, now)
            self.assertEqual(last_run_path.read_text(), "2026-06-12T12:00:00+00:00")


class TestRunGh(unittest.TestCase):
    def test_run_gh_returns_json(self) -> None:
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

    def test_run_gh_empty_stdout_returns_empty_list(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = run_gh(["api", "repos/owner/repo/issues/comments"])
            self.assertEqual(result, [])


class TestFetchPrs(unittest.TestCase):
    def test_fetch_prs_filters_fields(self) -> None:
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
    def test_fetch_issues_filters_fields(self) -> None:
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
    def test_fetch_comments_filters_fields(self) -> None:
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
    def test_split_by_day_groups_items_by_day(self) -> None:
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
    def test_write_daily_data_appends_to_existing(self) -> None:
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

    def test_write_daily_data_deduplicates(self) -> None:
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

    def test_write_daily_data_includes_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            day = "2026-06-14"
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
                            "events": [
                                {
                                    "id": "12345",
                                    "event": "renamed",
                                    "created_at": "2026-06-14T10:00:00Z",
                                    "actor": "kaungkhantko",
                                    "issue_number": 42,
                                    "pr_number": 42,
                                    "source": "pull_request",
                                    "details": {"changes": {"title": {"from": "old"}}}
                                }
                            ]
                        }
                    }
                }
            }

            write_daily_data(data_dir, day, new_data)

            updated = json.loads((data_dir / f"{day}.json").read_text())
            user_data = updated["repos"]["owner/repo-1"]["users"]["kaungkhantko"]
            self.assertEqual(user_data["prs"], [{"number": 1, "title": "PR One"}])
            self.assertEqual(len(user_data["events"]), 1)
            self.assertEqual(user_data["events"][0]["event"], "renamed")
            self.assertEqual(user_data["events"][0]["source"], "pull_request")


class TestFetchCommits(unittest.TestCase):
    def test_fetch_commits_filters_fields(self) -> None:
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


class TestExtractEvent(unittest.TestCase):
    def test_extract_event_renamed(self) -> None:
        from src.scrape import _extract_event

        raw_event = {
            "id": "12345",
            "event": "renamed",
            "created_at": "2026-06-14T10:00:00Z",
            "actor": {"login": "kaungkhantko"},
            "issue": {"number": 42},
            "url": "https://api.github.com/repos/owner/repo/issues/events/12345",
            "changes": {"title": {"from": "Old Title"}},
        }
        fields = ["id", "event", "created_at", "actor", "issue_number", "pr_number", "source", "details"]
        pr_numbers = {42}

        result = _extract_event(raw_event, fields, pr_numbers, issue_number=42)

        self.assertEqual(result, {
            "id": "12345",
            "event": "renamed",
            "created_at": "2026-06-14T10:00:00Z",
            "actor": "kaungkhantko",
            "issue_number": 42,
            "pr_number": 42,
            "source": "pull_request",
            "details": {"changes": {"title": {"from": "Old Title"}}},
        })

    def test_extract_event_edited(self) -> None:
        from src.scrape import _extract_event

        raw_event = {
            "id": "67890",
            "event": "edited",
            "created_at": "2026-06-14T11:00:00Z",
            "actor": {"login": "kaungkhantko"},
            "issue": {"number": 7},
            "url": "https://api.github.com/repos/owner/repo/issues/events/67890",
            "changes": {"body": {"from": "old body text"}},
        }
        fields = ["id", "event", "created_at", "actor", "issue_number", "pr_number", "source", "details"]
        pr_numbers = set()

        result = _extract_event(raw_event, fields, pr_numbers, issue_number=7)

        self.assertEqual(result, {
            "id": "67890",
            "event": "edited",
            "created_at": "2026-06-14T11:00:00Z",
            "actor": "kaungkhantko",
            "issue_number": 7,
            "pr_number": 7,
            "source": "issue",
            "details": {"changes": {"body": {"from": "old body text"}}},
        })

    def test_extract_event_labeled(self) -> None:
        from src.scrape import _extract_event

        raw_event = {
            "id": "11111",
            "event": "labeled",
            "created_at": "2026-06-14T12:00:00Z",
            "actor": {"login": "kaungkhantko"},
            "issue": {"number": 5},
            "url": "https://api.github.com/repos/owner/repo/issues/events/11111",
            "label": {"name": "bug"},
        }
        fields = ["id", "event", "created_at", "actor", "issue_number", "pr_number", "source", "details"]
        pr_numbers = set()

        result = _extract_event(raw_event, fields, pr_numbers, issue_number=5)

        self.assertEqual(result["event"], "labeled")
        self.assertEqual(result["source"], "issue")
        self.assertEqual(result["details"], {"label": {"name": "bug"}})

    def test_extract_event_filters_fields(self) -> None:
        from src.scrape import _extract_event

        raw_event = {
            "id": "12345",
            "event": "renamed",
            "created_at": "2026-06-14T10:00:00Z",
            "actor": {"login": "kaungkhantko"},
            "issue": {"number": 42},
            "url": "https://api.github.com/repos/owner/repo/issues/events/12345",
            "changes": {"title": {"from": "Old Title"}},
        }
        fields = ["id", "event", "created_at"]
        pr_numbers = {42}

        result = _extract_event(raw_event, fields, pr_numbers, issue_number=42)

        self.assertEqual(set(result.keys()), {"id", "event", "created_at"})

    def test_extract_event_with_real_api_shape(self) -> None:
        from src.scrape import _extract_event

        raw_event = {
            "id": 26682461804,
            "node_id": "RRE_lADO...",
            "url": "https://api.github.com/repos/owner/repo/issues/events/26682461804",
            "actor": {"login": "kaungkhantko"},
            "event": "review_requested",
            "created_at": "2026-06-15T16:18:01Z",
            "review_requester": {"login": "kaungkhantko"},
            "requested_reviewer": {"login": "Copilot"},
            "commit_id": None,
            "commit_url": None,
            "performed_via_github_app": None,
        }
        fields = ["id", "event", "created_at", "actor", "issue_number", "pr_number", "source", "details"]
        pr_numbers = {42}
        issue_number = 42

        result = _extract_event(raw_event, fields, pr_numbers, issue_number)

        self.assertEqual(result["issue_number"], 42)
        self.assertEqual(result["pr_number"], 42)
        self.assertEqual(result["source"], "pull_request")
        self.assertEqual(result["event"], "review_requested")
        self.assertEqual(result["actor"], "kaungkhantko")


class TestItemId(unittest.TestCase):
    def test_item_id_prefers_id_field(self) -> None:
        from src.scrape import _item_id

        item = {"id": "12345", "number": 42, "sha": "abc", "url": "https://example.com"}
        self.assertEqual(_item_id(item), "12345")

    def test_item_id_falls_back_to_number(self) -> None:
        from src.scrape import _item_id

        item = {"number": 42, "url": "https://example.com"}
        self.assertEqual(_item_id(item), "42")

    def test_item_id_falls_back_to_sha(self) -> None:
        from src.scrape import _item_id

        item = {"sha": "abc123"}
        self.assertEqual(_item_id(item), "abc123")

    def test_item_id_falls_back_to_url(self) -> None:
        from src.scrape import _item_id

        item = {"url": "https://example.com/foo"}
        self.assertEqual(_item_id(item), "https://example.com/foo")


class TestFetchEvents(unittest.TestCase):
    def test_fetch_events_filters_by_actor_and_window(self) -> None:
        from src.scrape import fetch_events
        from datetime import datetime, timezone

        raw_events = [
            {
                "id": "1",
                "event": "renamed",
                "created_at": "2026-06-14T10:00:00Z",
                "actor": {"login": "kaungkhantko"},
                "issue": {"number": 42},
                "url": "https://api.github.com/.../events/1",
                "changes": {"title": {"from": "old"}},
            },
            {
                "id": "2",
                "event": "labeled",
                "created_at": "2026-06-14T11:00:00Z",
                "actor": {"login": "otheruser"},
                "issue": {"number": 42},
                "url": "https://api.github.com/.../events/2",
                "label": {"name": "bug"},
            },
            {
                "id": "3",
                "event": "closed",
                "created_at": "2026-06-10T11:00:00Z",
                "actor": {"login": "kaungkhantko"},
                "issue": {"number": 42},
                "url": "https://api.github.com/.../events/3",
            },
        ]
        fields = ["id", "event", "created_at", "actor", "issue_number", "pr_number", "source", "details"]
        since = "2026-06-14T00:00:00Z"
        until = "2026-06-14T23:59:59Z"
        pr_numbers = {42}
        variants = ["kaungkhantko"]

        result = fetch_events(raw_events, fields, since, until, pr_numbers, variants, issue_number=42)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "1")
        self.assertEqual(result[0]["event"], "renamed")
        self.assertEqual(result[0]["actor"], "kaungkhantko")


class TestFetchRepoActivityEvents(unittest.TestCase):
    def test_fetch_repo_activity_includes_events(self) -> None:
        from src.scrape import fetch_repo_activity

        prs_raw = [{"number": 42, "title": "PR", "state": "open", "url": "https://.../pull/42",
                    "createdAt": "2026-06-14T10:00:00Z", "closedAt": None, "mergedAt": None,
                    "author": {"login": "kaungkhantko"}, "labels": [], "body": ""}]
        issues_raw = []
        comments_raw = [{"issue": {"number": 7}, "pullRequest": None,
                         "body": "comment", "url": "https://...",
                         "createdAt": "2026-06-14T10:00:00Z",
                         "author": {"login": "kaungkhantko"}}]
        events_pr42 = [{"id": "1", "event": "renamed", "created_at": "2026-06-14T10:00:00Z",
                        "actor": {"login": "kaungkhantko"}, "issue": {"number": 42},
                        "url": "https://api.github.com/.../events/1",
                        "changes": {"title": {"from": "old"}}}]
        events_issue7 = [{"id": "2", "event": "labeled", "created_at": "2026-06-14T11:00:00Z",
                          "actor": {"login": "kaungkhantko"}, "issue": {"number": 7},
                          "url": "https://api.github.com/.../events/2",
                          "label": {"name": "bug"}}]

        def mock_run_gh(args):
            if "issues/42/events" in str(args):
                return events_pr42
            if "issues/7/events" in str(args):
                return events_issue7
            if args[0] == "pr" and "list" in args:
                return prs_raw
            if "issues/comments" in str(args):
                return comments_raw
            if args[0] == "issue" and "list" in args:
                return issues_raw
            return []

        activity_types = ["prs", "issues", "comments", "events"]
        fields = {
            "prs": ["number", "title", "state", "url", "created_at", "closed_at", "merged_at", "author", "labels", "body"],
            "issues": ["number", "title", "state", "url", "created_at", "closed_at", "author", "labels", "body"],
            "comments": ["type", "issue_number", "pr_number", "body", "url", "created_at", "author"],
            "events": ["id", "event", "created_at", "actor", "issue_number", "pr_number", "source", "details"],
        }

        with patch("src.scrape.run_gh", side_effect=mock_run_gh):
            result = fetch_repo_activity(
                repo="owner/repo",
                identities=[{"key": "kaungkhantko", "variants": ["kaungkhantko"]}],
                activity_types=activity_types,
                fields=fields,
                since="2026-06-14T00:00:00Z",
                until="2026-06-14T23:59:59Z",
            )

        events = result["kaungkhantko"]["events"]
        self.assertEqual(len(events), 2)
        event_ids = {e["id"] for e in events}
        self.assertEqual(event_ids, {"1", "2"})
        sources = {e["source"] for e in events}
        self.assertEqual(sources, {"pull_request", "issue"})


class TestLoadIdentities(unittest.TestCase):
    def test_load_identities_from_config(self) -> None:
        from src.scrape import _load_identities

        config = {
            "identities": {
                "kaungkhantko": ["kaungkhantko", "kaungkhant.ko", "Kaung Khant Ko"]
            }
        }
        self.assertEqual(_load_identities(config), [
            {"key": "kaungkhantko", "variants": ["kaungkhantko", "kaungkhant.ko", "Kaung Khant Ko"]},
        ])

    def test_load_identities_falls_back_to_users(self) -> None:
        from src.scrape import _load_identities

        config = {"users": ["alice", "bob"]}
        self.assertEqual(_load_identities(config), [
            {"key": "alice", "variants": ["alice"]},
            {"key": "bob", "variants": ["bob"]},
        ])


class TestIdentityMatching(unittest.TestCase):
    def test_identity_token_normalizes_case_and_punctuation(self) -> None:
        from src.scrape import _identity_token

        self.assertEqual(_identity_token("Kaung Khant Ko"), "kaungkhantko")
        self.assertEqual(_identity_token("kaungkhant.ko"), "kaungkhantko")
        self.assertEqual(_identity_token("kaungkhantko@gmail.com"), "kaungkhantkogmailcom")

    def test_matches_identity_by_name_email_login(self) -> None:
        from src.scrape import _matches_identity

        variants = ["kaungkhantko", "kaungkhant.ko", "Kaung Khant Ko"]
        self.assertTrue(_matches_identity(["Kaung Khant Ko"], variants))
        self.assertTrue(_matches_identity(["kaungkhant.ko"], variants))
        self.assertTrue(_matches_identity(["kaungkhantko"], variants))
        self.assertFalse(_matches_identity(["somebody-else"], variants))

    def test_commit_identity_matches_variants(self) -> None:
        from src.scrape import _commit_identity_matches

        variants = ["kaungkhantko", "kaungkhant.ko", "Kaung Khant Ko"]
        by_name = {
            "sha": "a",
            "commit": {
                "author": {"name": "Kaung Khant Ko", "email": "kaungkhantko@gmail.com"},
                "committer": {"name": "Kaung Khant Ko", "email": "kaungkhantko@gmail.com"},
            },
            "author": None,
        }
        by_login = {
            "sha": "b",
            "commit": {
                "author": {"name": "Other", "email": "other@example.com"},
                "committer": {"name": "Other", "email": "other@example.com"},
            },
            "author": {"login": "kaungkhantko"},
        }
        stranger = {
            "sha": "c",
            "commit": {
                "author": {"name": "Somebody Else", "email": "someone@example.com"},
                "committer": {"name": "Somebody Else", "email": "someone@example.com"},
            },
            "author": {"login": "somebody-else"},
        }
        self.assertTrue(_commit_identity_matches(by_name, variants))
        self.assertTrue(_commit_identity_matches(by_login, variants))
        self.assertFalse(_commit_identity_matches(stranger, variants))


class TestFetchRepoActivityIdentities(unittest.TestCase):
    def test_merges_prs_across_variant_logins(self) -> None:
        from src.scrape import fetch_repo_activity

        def prs_for(login: str) -> list[dict]:
            number = 1 if login == "kaungkhantko" else 2
            return [{
                "number": number,
                "title": "PR",
                "state": "open",
                "url": f"https://github.com/owner/repo/pull/{number}",
                "createdAt": "2026-06-14T10:00:00Z",
                "closedAt": None,
                "mergedAt": None,
                "author": {"login": login},
                "labels": [],
                "body": "",
            }]

        def mock_run_gh(args):
            if args[0] == "pr" and "list" in args:
                return prs_for(args[args.index("--author") + 1])
            if args[0] == "issue" and "list" in args:
                return []
            if "issues/comments" in str(args):
                return []
            return []

        fields = {
            "prs": ["number", "title", "state", "url", "created_at", "closed_at", "merged_at", "author", "labels", "body"],
            "issues": ["number"],
            "comments": ["type"],
        }
        identities = [{"key": "kaungkhantko", "variants": ["kaungkhantko", "kaungkhant.ko"]}]

        with patch("src.scrape.run_gh", side_effect=mock_run_gh):
            result = fetch_repo_activity(
                repo="owner/repo",
                identities=identities,
                activity_types=["prs", "issues", "comments"],
                fields=fields,
                since="2026-06-14T00:00:00Z",
                until="2026-06-14T23:59:59Z",
            )

        prs = result["kaungkhantko"]["prs"]
        self.assertEqual({pr["number"] for pr in prs}, {1, 2})

    def test_filters_commits_by_identity_variants(self) -> None:
        from src.scrape import fetch_repo_activity

        commits = [
            {
                "sha": "aaa",
                "commit": {
                    "author": {"name": "Kaung Khant Ko", "email": "kaungkhantko@gmail.com"},
                    "committer": {"name": "Kaung Khant Ko", "email": "kaungkhantko@gmail.com"},
                },
                "author": {"login": "kaungkhantko"},
                "html_url": "https://github.com/owner/repo/commit/aaa",
            },
            {
                "sha": "bbb",
                "commit": {
                    "author": {"name": "Somebody Else", "email": "someone@example.com"},
                    "committer": {"name": "Somebody Else", "email": "someone@example.com"},
                },
                "author": None,
                "html_url": "https://github.com/owner/repo/commit/bbb",
            },
        ]

        def mock_run_gh(args):
            if "repos/owner/repo/commits" in str(args):
                return commits
            return []

        fields = {"commits": ["sha", "message", "url", "date", "author", "files_changed"]}
        identities = [{"key": "kaungkhantko", "variants": ["kaungkhantko", "kaungkhant.ko", "Kaung Khant Ko"]}]

        with patch("src.scrape.run_gh", side_effect=mock_run_gh):
            result = fetch_repo_activity(
                repo="owner/repo",
                identities=identities,
                activity_types=["commits"],
                fields=fields,
                since="2026-06-14T00:00:00Z",
                until="2026-06-14T23:59:59Z",
            )

        shas = {commit["sha"] for commit in result["kaungkhantko"]["commits"]}
        self.assertEqual(shas, {"aaa"})


class TestFetchReviewComments(unittest.TestCase):
    def test_fetch_review_comments_filters_by_identity_and_window(self) -> None:
        from src.scrape import fetch_review_comments

        raw = [
            {
                "id": 1001,
                "body": "ACL rows missing here",
                "created_at": "2026-08-05T19:28:54Z",
                "user": {"login": "kaungkhantko"},
                "path": "ifm_hr/security/ir.model.access.csv",
                "line": 8,
                "html_url": "https://github.com/owner/repo/pull/179#discussion_r1001",
            },
            {
                "id": 1002,
                "body": "from another user",
                "created_at": "2026-08-05T19:29:00Z",
                "user": {"login": "walterhoops"},
                "path": "ifm_hr/models/res_users.py",
                "line": None,
                "html_url": "https://github.com/owner/repo/pull/179#discussion_r1002",
            },
            {
                "id": 1003,
                "body": "outside window",
                "created_at": "2026-08-07T20:23:05Z",
                "user": {"login": "kaungkhantko"},
                "path": "ifm_hr/models/res_users.py",
                "line": None,
                "html_url": "https://github.com/owner/repo/pull/179#discussion_r1003",
            },
        ]
        fields = ["type", "pr_number", "body", "url", "created_at", "author", "path", "line"]
        since = "2026-08-05T00:00:00Z"
        until = "2026-08-05T23:59:59Z"
        variants = ["kaungkhantko", "kaungkhant.ko", "Kaung Khant Ko"]

        result = fetch_review_comments(raw, fields, since, until, variants)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "review_comment")
        self.assertEqual(result[0]["pr_number"], 179)
        self.assertEqual(result[0]["path"], "ifm_hr/security/ir.model.access.csv")
        self.assertEqual(result[0]["line"], 8)
        self.assertEqual(result[0]["author"], "kaungkhantko")


class TestFetchReviews(unittest.TestCase):
    def test_fetch_reviews_filters_by_identity_window_and_fields(self) -> None:
        from src.scrape import fetch_reviews

        raw = [
            {"id": 1, "state": "COMMENTED", "submitted_at": "2026-08-05T19:28:54Z",
             "user": {"login": "kaungkhantko"},
             "html_url": "https://github.com/owner/repo/pull/179#pullrequestreview-1",
             "body": "The decouple is good work."},
            {"id": 2, "state": "APPROVED", "submitted_at": "2026-08-05T20:00:00Z",
             "user": {"login": "walterhoops"},
             "html_url": "https://github.com/owner/repo/pull/179#pullrequestreview-2",
             "body": ""},
        ]
        fields = ["id", "state", "submitted_at", "author", "url", "body", "pr_number"]
        since = "2026-08-05T00:00:00Z"
        until = "2026-08-05T23:59:59Z"
        variants = ["kaungkhantko"]

        result = fetch_reviews(raw, fields, since, until, variants, pr_number=179)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], {
            "id": 1,
            "state": "COMMENTED",
            "submitted_at": "2026-08-05T19:28:54Z",
            "author": "kaungkhantko",
            "url": "https://github.com/owner/repo/pull/179#pullrequestreview-1",
            "body": "The decouple is good work.",
            "pr_number": 179,
        })


class TestFetchRepoActivityReviews(unittest.TestCase):
    def test_discovers_reviewed_prs_and_tracks_reviews(self) -> None:
        from src.scrape import fetch_repo_activity

        prs_raw = [{"number": 42, "title": "PR", "state": "open",
                    "url": "https://github.com/owner/repo/pull/42",
                    "createdAt": "2026-08-05T10:00:00Z", "closedAt": None, "mergedAt": None,
                    "author": {"login": "kaungkhantko"}, "labels": [], "body": ""}]
        reviews_raw = [
            {"id": 1, "state": "COMMENTED", "submitted_at": "2026-08-05T19:28:54Z",
             "user": {"login": "kaungkhantko"},
             "html_url": "https://github.com/owner/repo/pull/179#pullrequestreview-1",
             "body": "ACL rows missing."},
            {"id": 2, "state": "COMMENTED", "submitted_at": "2026-08-05T20:00:00Z",
             "user": {"login": "walterhoops"},
             "html_url": "https://github.com/owner/repo/pull/179#pullrequestreview-2",
             "body": ""},
        ]
        review_comments_raw = [
            {"id": 1001, "body": "ACL rows missing here", "created_at": "2026-08-05T19:28:54Z",
             "user": {"login": "kaungkhantko"}, "path": "ifm_hr/security/ir.model.access.csv", "line": 8,
             "html_url": "https://github.com/owner/repo/pull/179#discussion_r1001"},
        ]

        def mock_run_gh(args):
            if args[0] == "search" and "prs" in args:
                return [{"number": 179}] if "kaungkhantko" in args else []
            if args[0] == "pr" and "list" in args:
                return prs_raw
            if args[0] == "issue" and "list" in args:
                return []
            if "issues/comments" in str(args):
                return []
            if "pulls/179/reviews" in str(args):
                return reviews_raw
            if "pulls/comments" in str(args):
                return review_comments_raw
            return []

        fields = {
            "prs": ["number", "title", "state", "url", "created_at", "closed_at", "merged_at", "author", "labels", "body"],
            "issues": ["number"],
            "comments": ["type", "issue_number", "pr_number", "body", "url", "created_at", "author", "path", "line"],
            "reviews": ["id", "state", "submitted_at", "author", "url", "body", "pr_number"],
        }
        identities = [{"key": "kaungkhantko", "variants": ["kaungkhantko", "kaungkhant.ko"]}]

        with patch("src.scrape.run_gh", side_effect=mock_run_gh):
            result = fetch_repo_activity(
                repo="owner/repo",
                identities=identities,
                activity_types=["prs", "comments", "reviews"],
                fields=fields,
                since="2026-08-05T00:00:00Z",
                until="2026-08-05T23:59:59Z",
            )

        user_data = result["kaungkhantko"]
        self.assertEqual(len(user_data["reviews"]), 1)
        self.assertEqual(user_data["reviews"][0]["pr_number"], 179)
        self.assertEqual(user_data["reviews"][0]["state"], "COMMENTED")
        self.assertEqual(user_data["comments"][0]["type"], "review_comment")
        self.assertEqual(user_data["comments"][0]["pr_number"], 179)


class TestResolveRepoName(unittest.TestCase):
    def test_resolve_returns_canonical_name_when_repo_moved(self) -> None:
        from src.scrape import _resolve_repo_name

        with patch("src.scrape.run_gh", return_value={"full_name": "mutualpolydynamics/Odoo-IFM"}):
            self.assertEqual(_resolve_repo_name("kaungkhantko/Odoo-IFM"), "mutualpolydynamics/Odoo-IFM")

    def test_resolve_falls_back_when_query_fails(self) -> None:
        from src.scrape import _resolve_repo_name

        with patch("src.scrape.run_gh", side_effect=RuntimeError("gh command failed")):
            self.assertEqual(_resolve_repo_name("owner/repo"), "owner/repo")

    def test_resolve_falls_back_on_empty_response(self) -> None:
        from src.scrape import _resolve_repo_name

        with patch("src.scrape.run_gh", return_value=[]):
            self.assertEqual(_resolve_repo_name("owner/repo"), "owner/repo")

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_config(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    config["data_dir"] = os.path.expanduser(config["data_dir"])
    config["log_file"] = os.path.expanduser(config["log_file"])
    return config


def get_date_range(last_run: datetime | None, bootstrap_days: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    if last_run is None:
        since = now - timedelta(days=bootstrap_days)
    else:
        since = last_run
    return since.isoformat(), now.isoformat()


def read_last_run(last_run_path: Path) -> datetime | None:
    if not last_run_path.exists():
        return None
    text = last_run_path.read_text().strip()
    return datetime.fromisoformat(text)


def write_last_run(last_run_path: Path, timestamp: datetime) -> None:
    last_run_path.parent.mkdir(parents=True, exist_ok=True)
    last_run_path.write_text(timestamp.isoformat())


def run_gh(args: list[str]) -> list[dict] | dict:
    cmd = ["gh"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr}")
    stdout = result.stdout.strip()
    if not stdout:
        return []
    return json.loads(stdout)


def _normalize_field(key: str) -> str:
    return key.replace("createdAt", "created_at").replace("closedAt", "closed_at").replace("mergedAt", "merged_at")


def _extract_value(value: Any):
    if isinstance(value, dict):
        if "login" in value:
            return value["login"]
        if "name" in value:
            return value["name"]
    if isinstance(value, list):
        return [_extract_value(v) for v in value]
    return value


def _filter_fields(raw: dict, fields: list[str]) -> dict:
    result = {}
    for key, value in raw.items():
        normalized_key = _normalize_field(key)
        if normalized_key in fields:
            result[normalized_key] = _extract_value(value)
    return result


def fetch_prs(raw_prs: list[dict], fields: list[str]) -> list[dict]:
    return [_filter_fields(pr, fields) for pr in raw_prs]


def fetch_issues(raw_issues: list[dict], fields: list[str]) -> list[dict]:
    return [_filter_fields(issue, fields) for issue in raw_issues]


def _comment_type(raw: dict) -> str:
    if raw.get("pullRequest"):
        return "pr_comment"
    return "issue_comment"


def _extract_comment(raw: dict, fields: list[str]) -> dict:
    result = {}
    if "type" in fields:
        result["type"] = _comment_type(raw)
    if "issue_number" in fields:
        issue = raw.get("issue") or {}
        result["issue_number"] = issue.get("number")
    if "pr_number" in fields:
        pr = raw.get("pullRequest") or {}
        result["pr_number"] = pr.get("number")
    if "body" in fields:
        result["body"] = raw.get("body")
    if "url" in fields:
        result["url"] = raw.get("url")
    if "created_at" in fields:
        result["created_at"] = raw.get("createdAt")
    if "author" in fields:
        result["author"] = _extract_value(raw.get("author"))
    return result


def fetch_comments(raw_comments: list[dict], fields: list[str]) -> list[dict]:
    return [_extract_comment(c, fields) for c in raw_comments]


def _extract_commit(raw: dict, fields: list[str]) -> dict:
    result = {}
    commit_block = raw.get("commit", {})
    if "sha" in fields:
        result["sha"] = raw.get("sha")
    if "message" in fields:
        result["message"] = commit_block.get("message")
    if "url" in fields:
        result["url"] = raw.get("html_url")
    if "date" in fields:
        result["date"] = commit_block.get("committer", {}).get("date")
    if "author" in fields:
        result["author"] = _extract_value(raw.get("author"))
    if "files_changed" in fields:
        result["files_changed"] = [f["filename"] for f in raw.get("files", [])]
    return result


def fetch_commits(raw_commits: list[dict], fields: list[str]) -> list[dict]:
    return [_extract_commit(c, fields) for c in raw_commits]

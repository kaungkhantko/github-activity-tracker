import json
import os
import subprocess
from collections import defaultdict
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


def fetch_repo_activity(
    repo: str,
    users: list[str],
    activity_types: list[str],
    fields: dict[str, list[str]],
    since: str,
    until: str,
) -> dict:
    result = {user: {} for user in users}
    date_query = f"{since[:10]}..{until[:10]}"

    if "prs" in activity_types:
        for user in users:
            raw = run_gh([
                "pr", "list", "--repo", repo, "--state", "all",
                "--author", user,
                "--search", f"created:{date_query}",
                "--json", "number,title,state,url,createdAt,closedAt,mergedAt,author,labels,body",
            ])
            result[user]["prs"] = fetch_prs(raw, fields["prs"])

    if "issues" in activity_types:
        for user in users:
            raw = run_gh([
                "issue", "list", "--repo", repo, "--state", "all",
                "--author", user,
                "--search", f"created:{date_query}",
                "--json", "number,title,state,url,createdAt,closedAt,author,labels,body",
            ])
            result[user]["issues"] = fetch_issues(raw, fields["issues"])

    if "comments" in activity_types:
        for user in users:
            raw = run_gh([
                "api", f"repos/{repo}/issues/comments",
                "-X", "GET", "-f", f"since={since}", "--paginate",
            ])
            filtered = [c for c in raw if _extract_value(c.get("author")) == user]
            result[user]["comments"] = fetch_comments(filtered, fields["comments"])

    if "commits" in activity_types:
        for user in users:
            raw = run_gh([
                "api", f"repos/{repo}/commits",
                "-X", "GET", "-f", f"author={user}", "-f", f"since={since}", "-f", f"until={until}", "--paginate",
            ])
            result[user]["commits"] = fetch_commits(raw, fields["commits"])

    return result


def split_by_day(items: list[dict]) -> dict[str, list[dict]]:
    by_day = defaultdict(list)
    for item in items:
        date_key = item.get("created_at") or item.get("date")
        if date_key:
            day = date_key[:10]
            by_day[day].append(item)
    return dict(by_day)


def _load_day_file(data_dir: Path, day: str) -> dict:
    day_path = data_dir / f"{day}.json"
    if day_path.exists():
        return json.loads(day_path.read_text())
    return {"date": day, "repos": {}}


def _item_id(item: dict) -> str:
    for key in ["number", "sha", "url"]:
        if key in item:
            return str(item[key])
    return str(item)


def write_daily_data(data_dir: Path, day: str, repo_data: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    day_data = _load_day_file(data_dir, day)

    for repo, repo_payload in repo_data.items():
        if repo not in day_data["repos"]:
            day_data["repos"][repo] = {"users": {}}

        for user, activities in repo_payload.get("users", {}).items():
            if user not in day_data["repos"][repo]["users"]:
                day_data["repos"][repo]["users"][user] = {}

            for activity_type, items in activities.items():
                existing = day_data["repos"][repo]["users"][user].get(activity_type, [])
                existing_ids = {_item_id(i) for i in existing}
                for item in items:
                    item_id = _item_id(item)
                    if item_id not in existing_ids:
                        existing.append(item)
                        existing_ids.add(item_id)
                day_data["repos"][repo]["users"][user][activity_type] = existing

    day_path = data_dir / f"{day}.json"
    day_path.write_text(json.dumps(day_data, indent=2))


def main() -> None:
    import logging

    config_path = Path(__file__).resolve().parent.parent / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"{config_path} not found. Copy example-config.json to config.json and edit it."
        )
    config = load_config(config_path)

    data_dir = Path(config["data_dir"])
    log_file = Path(config["log_file"])
    state_dir = log_file.parent
    last_run_path = state_dir / "last_run.txt"

    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    last_run = read_last_run(last_run_path)
    since, until = get_date_range(last_run, config["bootstrap_days"])

    logging.info(f"Starting scrape from {since} to {until}")

    all_activity = {}
    for repo in config["repos"]:
        logging.info(f"Fetching activity for {repo}")
        all_activity[repo] = fetch_repo_activity(
            repo,
            config["users"],
            config["activity_types"],
            config["fields"],
            since,
            until,
        )

    # Split by day and write
    repo_day_data = {}
    for repo, users_data in all_activity.items():
        for user, activities in users_data.items():
            for activity_type, items in activities.items():
                by_day = split_by_day(items)
                for day, day_items in by_day.items():
                    if day not in repo_day_data:
                        repo_day_data[day] = {}
                    if repo not in repo_day_data[day]:
                        repo_day_data[day][repo] = {"users": {}}
                    if user not in repo_day_data[day][repo]["users"]:
                        repo_day_data[day][repo]["users"][user] = {}
                    repo_day_data[day][repo]["users"][user][activity_type] = day_items

    for day, repo_data in repo_day_data.items():
        write_daily_data(data_dir, day, repo_data)

    write_last_run(last_run_path, datetime.now(timezone.utc))
    logging.info("Scrape complete")


if __name__ == "__main__":
    main()

import json
import logging
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
    since = last_run if last_run else now - timedelta(days=bootstrap_days)
    return since.isoformat(), now.isoformat()


def read_last_run(last_run_path: Path) -> datetime | None:
    if not last_run_path.exists():
        return None
    return datetime.fromisoformat(last_run_path.read_text().strip())


def write_last_run(last_run_path: Path, timestamp: datetime) -> None:
    last_run_path.parent.mkdir(parents=True, exist_ok=True)
    last_run_path.write_text(timestamp.isoformat())


def run_gh(args: list[str]) -> list[dict] | dict:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr}")
    stdout = result.stdout.strip()
    return json.loads(stdout) if stdout else []


def _to_snake_case(key: str) -> str:
    return key.replace("createdAt", "created_at").replace("closedAt", "closed_at").replace("mergedAt", "merged_at")


def _unwrap_value(value: Any):
    if isinstance(value, dict):
        if "login" in value:
            return value["login"]
        if "name" in value:
            return value["name"]
    if isinstance(value, list):
        return [_unwrap_value(v) for v in value]
    return value


def _filter_fields(item: dict, fields: list[str]) -> dict:
    return {
        _to_snake_case(key): _unwrap_value(value)
        for key, value in item.items()
        if _to_snake_case(key) in fields
    }


def fetch_prs(items: list[dict], fields: list[str]) -> list[dict]:
    return [_filter_fields(item, fields) for item in items]


def fetch_issues(items: list[dict], fields: list[str]) -> list[dict]:
    return [_filter_fields(item, fields) for item in items]


def _comment_type(comment: dict) -> str:
    return "pr_comment" if comment.get("pullRequest") else "issue_comment"


def _extract_comment(comment: dict, fields: list[str]) -> dict:
    issue = comment.get("issue") or {}
    pr = comment.get("pullRequest") or {}
    mapping = {
        "type": _comment_type(comment),
        "issue_number": issue.get("number"),
        "pr_number": pr.get("number"),
        "body": comment.get("body"),
        "url": comment.get("url"),
        "created_at": comment.get("createdAt"),
        "author": _unwrap_value(comment.get("author")),
    }
    return {field: mapping[field] for field in fields if field in mapping}


def fetch_comments(items: list[dict], fields: list[str]) -> list[dict]:
    return [_extract_comment(comment, fields) for comment in items]


def _extract_commit(commit: dict, fields: list[str]) -> dict:
    commit_block = commit.get("commit", {})
    mapping = {
        "sha": commit.get("sha"),
        "message": commit_block.get("message"),
        "url": commit.get("html_url"),
        "date": commit_block.get("committer", {}).get("date"),
        "author": _unwrap_value(commit.get("author")),
        "files_changed": [f["filename"] for f in commit.get("files", [])],
    }
    return {field: mapping[field] for field in fields if field in mapping}


def fetch_commits(items: list[dict], fields: list[str]) -> list[dict]:
    return [_extract_commit(commit, fields) for commit in items]


def fetch_repo_activity(
    repo: str,
    users: list[str],
    activity_types: list[str],
    fields: dict[str, list[str]],
    since: str,
    until: str,
) -> dict:
    activity_by_user = {user: {} for user in users}
    date_query = f"{since[:10]}..{until[:10]}"

    if "prs" in activity_types:
        for user in users:
            response = run_gh([
                "pr", "list", "--repo", repo, "--state", "all",
                "--author", user,
                "--search", f"created:{date_query}",
                "--json", "number,title,state,url,createdAt,closedAt,mergedAt,author,labels,body",
            ])
            activity_by_user[user]["prs"] = fetch_prs(response, fields["prs"])

    if "issues" in activity_types:
        for user in users:
            response = run_gh([
                "issue", "list", "--repo", repo, "--state", "all",
                "--author", user,
                "--search", f"created:{date_query}",
                "--json", "number,title,state,url,createdAt,closedAt,author,labels,body",
            ])
            activity_by_user[user]["issues"] = fetch_issues(response, fields["issues"])

    if "comments" in activity_types:
        response = run_gh([
            "api", f"repos/{repo}/issues/comments",
            "-X", "GET", "-f", f"since={since}", "--paginate",
        ])
        for user in users:
            user_comments = [c for c in response if _unwrap_value(c.get("author")) == user]
            activity_by_user[user]["comments"] = fetch_comments(user_comments, fields["comments"])

    if "commits" in activity_types:
        for user in users:
            response = run_gh([
                "api", f"repos/{repo}/commits",
                "-X", "GET", "-f", f"author={user}",
                "-f", f"since={since}", "-f", f"until={until}",
                "--paginate",
            ])
            activity_by_user[user]["commits"] = fetch_commits(response, fields["commits"])

    return activity_by_user


def split_by_day(items: list[dict]) -> dict[str, list[dict]]:
    by_day = defaultdict(list)
    for item in items:
        timestamp = item.get("created_at") or item.get("date")
        if timestamp:
            by_day[timestamp[:10]].append(item)
    return dict(by_day)


def _load_day_file(data_dir: Path, day: str) -> dict:
    day_path = data_dir / f"{day}.json"
    if day_path.exists():
        return json.loads(day_path.read_text())
    return {"date": day, "repos": {}}


def _item_id(item: dict) -> str:
    for key in ("number", "sha", "url"):
        if key in item:
            return str(item[key])
    return str(item)


def _ensure_path(nested: dict, keys: list[str]) -> dict:
    for key in keys:
        if key not in nested:
            nested[key] = {}
        nested = nested[key]
    return nested


def write_daily_data(data_dir: Path, day: str, repo_data: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    day_data = _load_day_file(data_dir, day)

    for repo, user_data in repo_data.items():
        repo_node = _ensure_path(day_data, ["repos", repo])
        for user, activities in user_data.get("users", {}).items():
            user_node = _ensure_path(repo_node, ["users", user])
            for activity_type, items in activities.items():
                existing = user_node.get(activity_type, [])
                seen = {_item_id(i) for i in existing}
                for item in items:
                    item_id = _item_id(item)
                    if item_id not in seen:
                        existing.append(item)
                        seen.add(item_id)
                user_node[activity_type] = existing

    (data_dir / f"{day}.json").write_text(json.dumps(day_data, indent=2))


def group_by_day(activity_by_repo: dict) -> dict:
    grouped = {}
    for repo, users_data in activity_by_repo.items():
        for user, activities in users_data.items():
            for activity_type, items in activities.items():
                for day, day_items in split_by_day(items).items():
                    repo_node = _ensure_path(grouped, [day, repo])
                    user_node = _ensure_path(repo_node, ["users", user])
                    user_node[activity_type] = day_items
    return grouped


def main() -> None:
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"{config_path} not found. Copy example-config.json to config.json and edit it."
        )
    config = load_config(config_path)

    data_dir = Path(config["data_dir"])
    log_file = Path(config["log_file"])
    last_run_path = log_file.parent / "last_run.txt"

    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    last_run = read_last_run(last_run_path)
    since, until = get_date_range(last_run, config["bootstrap_days"])

    logging.info(f"Starting scrape from {since} to {until}")

    activity_by_repo = {}
    for repo in config["repos"]:
        logging.info(f"Fetching activity for {repo}")
        activity_by_repo[repo] = fetch_repo_activity(
            repo,
            config["users"],
            config["activity_types"],
            config["fields"],
            since,
            until,
        )

    for day, repo_data in group_by_day(activity_by_repo).items():
        write_daily_data(data_dir, day, repo_data)

    write_last_run(last_run_path, datetime.now(timezone.utc))
    logging.info("Scrape complete")


if __name__ == "__main__":
    main()

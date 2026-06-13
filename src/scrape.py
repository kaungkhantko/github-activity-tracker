import json
import logging
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

Record = dict[str, Any]


def load_config(config_path: Path) -> Record:
    config: Record = json.loads(config_path.read_text())
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


def run_gh(args: list[str]) -> list[Record] | Record:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr}")
    stdout = result.stdout.strip()
    return json.loads(stdout) if stdout else []


def _to_snake_case(key: str) -> str:
    return key.replace("createdAt", "created_at").replace("closedAt", "closed_at").replace("mergedAt", "merged_at")


def _extract_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        if "login" in value:
            return value["login"]
        if "name" in value:
            return value["name"]
    if isinstance(value, list):
        return [_extract_scalar(v) for v in value]
    return value


def _normalize_item(item: Record, fields: list[str]) -> Record:
    return {
        _to_snake_case(key): _extract_scalar(value)
        for key, value in item.items()
        if _to_snake_case(key) in fields
    }


def make_field_extractor(mapping_builder: Callable[[Record], Record]) -> Callable[[Record, list[str]], Record]:
    def extract(item: Record, fields: list[str]) -> Record:
        mapping = mapping_builder(item)
        return {field: mapping[field] for field in fields if field in mapping}
    return extract


def make_fetcher(extractor: Callable[[Record, list[str]], Record]) -> Callable[[list[Record], list[str]], list[Record]]:
    def fetch(items: list[Record], fields: list[str]) -> list[Record]:
        return [extractor(item, fields) for item in items]
    return fetch


def _comment_type(comment: Record) -> str:
    return "pr_comment" if comment.get("pullRequest") else "issue_comment"


_extract_comment = make_field_extractor(lambda comment: {
    "type": _comment_type(comment),
    "issue_number": (comment.get("issue") or {}).get("number"),
    "pr_number": (comment.get("pullRequest") or {}).get("number"),
    "body": comment.get("body"),
    "url": comment.get("url"),
    "created_at": comment.get("createdAt"),
    "author": _extract_scalar(comment.get("author")),
})


_extract_commit = make_field_extractor(lambda commit: {
    "sha": commit.get("sha"),
    "message": commit.get("commit", {}).get("message"),
    "url": commit.get("html_url"),
    "date": commit.get("commit", {}).get("committer", {}).get("date"),
    "author": _extract_scalar(commit.get("author")),
    "files_changed": [f["filename"] for f in commit.get("files", [])],
})


fetch_prs = make_fetcher(_normalize_item)
fetch_issues = make_fetcher(_normalize_item)
fetch_comments = make_fetcher(_extract_comment)
fetch_commits = make_fetcher(_extract_commit)


def _to_camel_case(key: str) -> str:
    return key.replace("created_at", "createdAt").replace("closed_at", "closedAt").replace("merged_at", "mergedAt")


def _date_query(since: str, until: str) -> str:
    return f"{since[:10]}..{until[:10]}"


def _list_command(resource: str, repo: str, user: str, since: str, until: str, fields: list[str]) -> list[str]:
    gh_fields = ",".join(_to_camel_case(f) for f in fields)
    return [
        resource, "list", "--repo", repo, "--state", "all",
        "--author", user,
        "--search", f"created:{_date_query(since, until)}",
        "--json", gh_fields,
    ]


def _comment_command(repo: str, since: str) -> list[str]:
    return ["api", f"repos/{repo}/issues/comments", "-X", "GET", "-f", f"since={since}", "--paginate"]


def _commit_command(repo: str, user: str, since: str, until: str) -> list[str]:
    return [
        "api", f"repos/{repo}/commits",
        "-X", "GET", "-f", f"author={user}",
        "-f", f"since={since}", "-f", f"until={until}",
        "--paginate",
    ]


ACTIVITY_STRATEGIES: dict[str, dict[str, Any]] = {
    "prs": {
        "command": lambda repo, user, since, until, fields: _list_command("pr", repo, user, since, until, fields),
        "extractor": fetch_prs,
    },
    "issues": {
        "command": lambda repo, user, since, until, fields: _list_command("issue", repo, user, since, until, fields),
        "extractor": fetch_issues,
    },
    "comments": {
        "command": lambda repo, user, since, until, fields: _comment_command(repo, since),
        "extractor": fetch_comments,
        "author_getter": lambda item: _extract_scalar(item.get("author")),
    },
    "commits": {
        "command": lambda repo, user, since, until, fields: _commit_command(repo, user, since, until),
        "extractor": fetch_commits,
    },
}


def _fetch_activity_type(
    activity_type: str,
    repo: str,
    users: list[str],
    fields: list[str],
    since: str,
    until: str,
) -> dict[str, Record]:
    strategy = ACTIVITY_STRATEGIES[activity_type]
    result: dict[str, Record] = {}
    for user in users:
        response = run_gh(strategy["command"](repo, user, since, until, fields))
        if "author_getter" in strategy:
            response = [item for item in response if strategy["author_getter"](item) == user]
        result[user] = {activity_type: strategy["extractor"](response, fields)}
    return result


def fetch_repo_activity(
    repo: str,
    users: list[str],
    activity_types: list[str],
    fields: dict[str, list[str]],
    since: str,
    until: str,
) -> dict[str, Record]:
    """Fetch activity from GitHub. This function is impure — it calls `run_gh`."""
    activity_by_user: dict[str, Record] = {user: {} for user in users}
    for activity_type in activity_types:
        fetched = _fetch_activity_type(activity_type, repo, users, fields[activity_type], since, until)
        activity_by_user = deep_merge_with(activity_by_user, fetched, _take_right)
    return activity_by_user


def split_by_day(items: list[Record]) -> dict[str, list[Record]]:
    by_day: dict[str, list[Record]] = defaultdict(list)
    for item in items:
        timestamp = item.get("created_at") or item.get("date")
        if timestamp:
            by_day[timestamp[:10]].append(item)
    return dict(by_day)


def _load_day_file(data_dir: Path, day: str) -> Record:
    day_path = data_dir / f"{day}.json"
    if day_path.exists():
        return json.loads(day_path.read_text())
    return {"date": day, "repos": {}}


def _item_id(item: Record) -> str:
    for key in ("number", "sha", "url"):
        if key in item:
            return str(item[key])
    return str(item)


def make_unique_merger(id_extractor: Callable[[Record], str]) -> Callable[[list[Record], list[Record]], list[Record]]:
    def merge_unique(left: list[Record], right: list[Record]) -> list[Record]:
        seen = {id_extractor(item) for item in left}
        merged = list(left)
        for item in right:
            item_id = id_extractor(item)
            if item_id not in seen:
                merged.append(item)
                seen.add(item_id)
        return merged
    return merge_unique


def _take_right(_left: Any, right: Any) -> Any:
    return right


def deep_merge_with(left: Any, right: Any, leaf_merger: Callable[[Any, Any], Any]) -> Any:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return leaf_merger(left, right)
    result = dict(left)
    for key, right_value in right.items():
        if key in result:
            result[key] = deep_merge_with(result[key], right_value, leaf_merger)
        else:
            result[key] = right_value
    return result


_merge_items = make_unique_merger(_item_id)


def _nested_group(entries: list[tuple[list[str], Any]]) -> Record:
    if not entries:
        return {}
    if len(entries[0][0]) == 1:
        return {key: value for [key], value in entries}
    groups: dict[str, list[tuple[list[str], Any]]] = defaultdict(list)
    for keys, value in entries:
        groups[keys[0]].append((keys[1:], value))
    return {key: _nested_group(group_entries) for key, group_entries in groups.items()}


def write_daily_data(data_dir: Path, day: str, repo_data: Record) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    day_data = _load_day_file(data_dir, day)
    updated = deep_merge_with(day_data, {"repos": repo_data}, _merge_items)
    (data_dir / f"{day}.json").write_text(json.dumps(updated, indent=2))


def group_by_day(activity_by_repo: dict[str, Record]) -> dict[str, Record]:
    entries = [
        ([day, repo, "users", user, activity_type], day_items)
        for repo, users_data in activity_by_repo.items()
        for user, activities in users_data.items()
        for activity_type, items in activities.items()
        for day, day_items in split_by_day(items).items()
    ]
    return _nested_group(entries)


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

    activity_by_repo: dict[str, Record] = {}
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

import json
import logging
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Final

Record = dict[str, Any]


class ActivityType(str, Enum):
    PRS = "prs"
    ISSUES = "issues"
    COMMENTS = "comments"
    COMMITS = "commits"
    EVENTS = "events"
    REVIEWS = "reviews"


def load_config(config_path: Path) -> Record:
    config: Record = json.loads(config_path.read_text())
    return {
        **config,
        "data_dir": os.path.expanduser(config["data_dir"]),
        "log_file": os.path.expanduser(config["log_file"]),
    }


def _load_identities(config: Record) -> list[Record]:
    if "identities" in config:
        return [
            {"key": key, "variants": list(variants)}
            for key, variants in config["identities"].items()
        ]
    return [{"key": user, "variants": [user]} for user in config.get("users", [])]


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


def _resolve_repo_name(repo: str) -> str:
    """Return the canonical owner/repo name; REST follows transfer redirects that GraphQL-backed commands ignore."""
    try:
        canonical = run_gh(["api", f"repos/{repo}", "--jq", ".full_name"])
    except RuntimeError:
        return repo
    return canonical if isinstance(canonical, str) and canonical else repo


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


def _identity_token(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _matches_identity(candidates: list[Any], variants: list[str]) -> bool:
    variant_tokens = {_identity_token(variant) for variant in variants}
    return any(_identity_token(candidate) in variant_tokens for candidate in candidates)


def _commit_identity_matches(commit: Record, variants: list[str]) -> bool:
    author = commit.get("author")
    candidates = [author.get("login")] if isinstance(author, dict) else []
    for party_key in ("author", "committer"):
        party = commit.get("commit", {}).get(party_key, {})
        candidates.extend([party.get("name"), party.get("email")])
    return _matches_identity([c for c in candidates if c], variants)


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


_PR_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"pull/(\d+)")


def _review_pr_number(comment: Record) -> int | None:
    match = _PR_NUMBER_RE.search(comment.get("html_url") or "")
    return int(match.group(1)) if match else None


_extract_review_comment = make_field_extractor(lambda comment: {
    "type": "review_comment",
    "pr_number": _review_pr_number(comment),
    "body": comment.get("body"),
    "url": comment.get("html_url"),
    "created_at": comment.get("created_at"),
    "author": _extract_scalar(comment.get("user")),
    "path": comment.get("path"),
    "line": comment.get("line"),
})


_extract_commit = make_field_extractor(lambda commit: {
    "sha": commit.get("sha"),
    "message": commit.get("commit", {}).get("message"),
    "url": commit.get("html_url"),
    "date": commit.get("commit", {}).get("committer", {}).get("date"),
    "author": _extract_scalar(commit.get("author")),
    "files_changed": [f["filename"] for f in commit.get("files", [])],
})


def _extract_event(event: Record, fields: list[str], pr_numbers: set[int], issue_number: int) -> Record:
    event_type = event.get("event")
    match event_type:
        case "renamed" | "edited":
            details = {"changes": event.get("changes", {})}
        case "labeled" | "unlabeled":
            details = {"label": event.get("label", {})}
        case "assigned" | "unassigned":
            details = {"assignee": event.get("assignee", {})}
        case _:
            details = {}

    mapping = {
        "id": event.get("id"),
        "event": event_type,
        "created_at": event.get("created_at"),
        "actor": _extract_scalar(event.get("actor")),
        "issue_number": issue_number,
        "pr_number": issue_number,
        "source": "pull_request" if issue_number in pr_numbers else "issue",
        "details": details,
    }
    return {field: mapping[field] for field in fields if field in mapping}


fetch_prs = make_fetcher(_normalize_item)
fetch_issues = make_fetcher(_normalize_item)
fetch_comments = make_fetcher(_extract_comment)
fetch_commits = make_fetcher(_extract_commit)


def _discover_issue_numbers(
    activity_by_user: dict[str, Record],
    identity: Record,
    repo: str,
) -> tuple[frozenset[int], frozenset[int]]:
    """Return (pr_numbers, issue_numbers) the user authored, commented on, or reviewed."""
    pr_numbers: set[int] = set()
    issue_numbers: set[int] = set()

    user_activity = activity_by_user.get(identity["key"], {})
    for pr in user_activity.get("prs", []):
        if "number" in pr:
            pr_numbers.add(pr["number"])
            issue_numbers.add(pr["number"])

    for issue in user_activity.get("issues", []):
        if "number" in issue:
            issue_numbers.add(issue["number"])

    for comment in user_activity.get("comments", []):
        if comment.get("type") == "pr_comment" and comment.get("pr_number"):
            pr_numbers.add(comment["pr_number"])
            issue_numbers.add(comment["pr_number"])
        elif comment.get("issue_number"):
            issue_numbers.add(comment["issue_number"])

    for variant in identity["variants"]:
        try:
            response = run_gh(["search", "prs", "--repo", repo, "--reviewed-by", variant, "--json", "number"])
        except RuntimeError:
            continue
        for pr in response:
            pr_numbers.add(pr["number"])
            issue_numbers.add(pr["number"])

    return frozenset(pr_numbers), frozenset(issue_numbers)


def fetch_events(
    raw_events: list[Record],
    fields: list[str],
    since: str,
    until: str,
    pr_numbers: set[int],
    variants: list[str],
    issue_number: int,
) -> list[Record]:
    result = []
    for event in raw_events:
        actor = _extract_scalar(event.get("actor"))
        if not _matches_identity([actor], variants):
            continue
        created_at = event.get("created_at", "")
        if not (since <= created_at <= until):
            continue
        result.append(_extract_event(event, fields, pr_numbers, issue_number))
    return result


def fetch_reviews(
    raw_reviews: list[Record],
    fields: list[str],
    since: str,
    until: str,
    variants: list[str],
    pr_number: int,
) -> list[Record]:
    result = []
    for review in raw_reviews:
        author = _extract_scalar(review.get("user"))
        if not _matches_identity([author], variants):
            continue
        submitted_at = review.get("submitted_at", "")
        if not (since <= submitted_at <= until):
            continue
        mapping = {
            "id": review.get("id"),
            "state": review.get("state"),
            "submitted_at": submitted_at,
            "author": author,
            "url": review.get("html_url"),
            "body": review.get("body"),
            "pr_number": pr_number,
        }
        result.append({field: mapping[field] for field in fields if field in mapping})
    return result


def fetch_review_comments(
    raw_comments: list[Record],
    fields: list[str],
    since: str,
    until: str,
    variants: list[str],
) -> list[Record]:
    result = []
    for comment in raw_comments:
        author = _extract_scalar(comment.get("user"))
        if not _matches_identity([author], variants):
            continue
        created_at = comment.get("created_at", "")
        if not (since <= created_at <= until):
            continue
        result.append(_extract_review_comment(comment, fields))
    return result


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


def _commit_command(repo: str, since: str, until: str) -> list[str]:
    return [
        "api", f"repos/{repo}/commits",
        "-X", "GET",
        "-f", f"since={since}", "-f", f"until={until}",
        "--paginate",
    ]


def _event_command(repo: str, issue_number: int) -> list[str]:
    return ["api", f"repos/{repo}/issues/{issue_number}/events", "-X", "GET", "--paginate"]


def _reviews_command(repo: str, pr_number: int) -> list[str]:
    return ["api", f"repos/{repo}/pulls/{pr_number}/reviews", "-X", "GET", "--paginate"]


def _review_comments_command(repo: str, since: str) -> list[str]:
    return ["api", f"repos/{repo}/pulls/comments", "-X", "GET", "-f", f"since={since}", "--paginate"]


ACTIVITY_STRATEGIES: Final[dict[ActivityType, dict[str, Any]]] = {
    ActivityType.PRS: {
        "command": lambda repo, user, since, until, fields: _list_command("pr", repo, user, since, until, fields),
        "extractor": fetch_prs,
    },
    ActivityType.ISSUES: {
        "command": lambda repo, user, since, until, fields: _list_command("issue", repo, user, since, until, fields),
        "extractor": fetch_issues,
    },
    ActivityType.COMMENTS: {
        "command": lambda repo, user, since, until, fields: _comment_command(repo, since),
        "extractor": fetch_comments,
        "identity_filter": lambda item, variants: _matches_identity(
            [_extract_scalar(item.get("author"))], variants,
        ),
    },
    ActivityType.COMMITS: {
        "command": lambda repo, user, since, until, fields: _commit_command(repo, since, until),
        "extractor": fetch_commits,
        "identity_filter": _commit_identity_matches,
    },
}


def _fetch_activity_type(
    activity_type: str,
    repo: str,
    identity: Record,
    fields: list[str],
    since: str,
    until: str,
) -> dict[str, Record]:
    strategy = ACTIVITY_STRATEGIES[activity_type]
    variants = identity["variants"]
    if "identity_filter" in strategy:
        response = run_gh(strategy["command"](repo, None, since, until, fields))
        response = [item for item in response if strategy["identity_filter"](item, variants)]
        items = strategy["extractor"](response, fields)
    else:
        raw: list[Record] = []
        for variant in variants:
            response = run_gh(strategy["command"](repo, variant, since, until, fields))
            raw = _merge_items(raw, response)
        items = strategy["extractor"](raw, fields)
    return {identity["key"]: {activity_type: items}}


def fetch_repo_activity(
    repo: str,
    identities: list[Record],
    activity_types: list[str],
    fields: dict[str, list[str]],
    since: str,
    until: str,
) -> dict[str, Record]:
    """Fetch activity from GitHub. This function is impure — it calls `run_gh`."""
    activity_by_identity: dict[str, Record] = {identity["key"]: {} for identity in identities}
    standard_types = [t for t in activity_types if t not in (ActivityType.EVENTS, ActivityType.REVIEWS)]
    for activity_type in standard_types:
        for identity in identities:
            fetched = _fetch_activity_type(
                activity_type, repo, identity, fields[activity_type], since, until,
            )
            activity_by_identity = deep_merge_with(activity_by_identity, fetched, _take_right)

    if ActivityType.EVENTS in activity_types or ActivityType.REVIEWS in activity_types:
        for identity in identities:
            key = identity["key"]
            pr_numbers, issue_numbers = _discover_issue_numbers(activity_by_identity, identity, repo)
            all_numbers = pr_numbers | issue_numbers

            if ActivityType.EVENTS in activity_types:
                event_fields = fields["events"]
                user_events: list[Record] = []
                for number in all_numbers:
                    raw = run_gh(_event_command(repo, number))
                    user_events.extend(fetch_events(
                        raw, event_fields, since, until, pr_numbers, identity["variants"], number,
                    ))
                activity_by_identity[key] = deep_merge_with(
                    activity_by_identity[key], {"events": user_events}, _take_right,
                )

            if ActivityType.REVIEWS in activity_types:
                review_fields = fields["reviews"]
                user_reviews: list[Record] = []
                for number in all_numbers:
                    raw = run_gh(_reviews_command(repo, number))
                    user_reviews.extend(fetch_reviews(
                        raw, review_fields, since, until, identity["variants"], number,
                    ))
                review_comments = fetch_review_comments(
                    run_gh(_review_comments_command(repo, since)),
                    fields["comments"], since, until, identity["variants"],
                )
                merged_comments = _merge_items(activity_by_identity[key].get("comments", []), review_comments)
                activity_by_identity[key] = deep_merge_with(
                    activity_by_identity[key],
                    {"reviews": user_reviews, "comments": merged_comments},
                    _take_right,
                )

    return activity_by_identity


def split_by_day(items: list[Record]) -> dict[str, list[Record]]:
    by_day: dict[str, list[Record]] = defaultdict(list)
    for item in items:
        timestamp = item.get("created_at") or item.get("date") or item.get("submitted_at")
        if timestamp:
            by_day[timestamp[:10]].append(item)
    return dict(by_day)


def _load_day_file(data_dir: Path, day: str) -> Record:
    day_path = data_dir / f"{day}.json"
    if day_path.exists():
        return json.loads(day_path.read_text())
    return {"date": day, "repos": {}}


def _item_id(item: Record) -> str:
    return next(
        (str(item[k]) for k in ("id", "number", "sha", "url") if k in item),
        str(item),
    )


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


_merge_items: Final[Callable[[list[Record], list[Record]], list[Record]]] = make_unique_merger(_item_id)


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
    config_dir = config_path.parent

    data_dir = config_dir / config["data_dir"]
    log_file = config_dir / config["log_file"]
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

    identities = _load_identities(config)

    activity_by_repo: dict[str, Record] = {}
    for repo in config["repos"]:
        canonical = _resolve_repo_name(repo)
        if canonical != repo:
            logging.warning(f"{repo} has moved to {canonical}; scraping the canonical name")
        logging.info(f"Fetching activity for {canonical}")
        activity_by_repo[canonical] = fetch_repo_activity(
            canonical,
            identities,
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

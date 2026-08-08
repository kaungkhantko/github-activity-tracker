# GitHub Activity Tracker

A Python-based cron scraper that collects GitHub activity (PRs, issues, comments, commits, reviews) for configured users across configured repositories and stores it locally as JSON.

## Requirements

- Python 3.10+
- GitHub CLI (`gh`) authenticated with access to target repositories
- cron

## Setup

```bash
./setup.sh
```

This copies `example-config.json` to `config.json` if it doesn't exist.

## Configuration

Edit `config.json` (or `example-config.json` to update the template):

- `repos`: list of repositories in `owner/repo` format. Each is resolved
  to its canonical owner each run — a transferred repo is scraped under
  its new name with a warning instead of silently returning nothing.
- `identities`: map of a canonical identity key to the login/name/email
  variants that count as that person. PRs and issues are queried per variant
  via `--author`; comments, commits, and events are matched locally against
  the variant list with case- and punctuation-insensitive comparison
  (so `kaungkhant.ko` matches `Kaung Khant Ko`). Falls back to a `users`
  list, treating each username as its own identity.
- `activity_types`: which activity types to collect (`prs`, `issues`, `comments`, `commits`, `events`, `reviews`)
- `reviews`: PR review summaries land in the `reviews` section; review
  comments land in `comments` with `type: "review_comment"` plus `path`
  and `line`. PRs the identity reviewed but did not author are
  discovered via `reviewed-by` search, so review activity on other
  people's PRs is captured.
- `fields`: fields to include for each activity type
- `cron_schedule`: cron expression for how often to run
- `data_dir`: local directory for JSON data
- `log_file`: path to log file
- `bootstrap_days`: days of history to fetch on first run

## Manual run

```bash
python3 src/scrape.py
```

## Data format

One JSON file per day is written to `data/`, named `YYYY-MM-DD.json`.

```json
{
  "date": "2026-06-12",
  "repos": {
    "owner/repo": {
      "users": {
        "owner": {
          "prs": [],
          "issues": [],
          "comments": [],
          "commits": [],
          "reviews": []
        }
      }
    }
  }
}
```

## Logs

Logs are written to the configured `log_file` (default: `logs/scrape.log`).

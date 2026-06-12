# GitHub Activity Tracker

A Python-based cron scraper that collects GitHub activity (PRs, issues, comments, commits) for configured users across configured repositories and stores it locally as JSON.

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

- `repos`: list of repositories in `owner/repo` format
- `users`: list of GitHub usernames to track
- `activity_types`: which activity types to collect (`prs`, `issues`, `comments`, `commits`)
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
          "commits": []
        }
      }
    }
  }
}
```

## Logs

Logs are written to the configured `log_file` (default: `logs/scrape.log`).

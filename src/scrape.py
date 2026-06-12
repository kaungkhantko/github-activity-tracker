import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


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

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.scrape import load_config


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

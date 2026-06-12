import json
import os
from pathlib import Path


def load_config(config_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    config["data_dir"] = os.path.expanduser(config["data_dir"])
    config["log_file"] = os.path.expanduser(config["log_file"])
    return config

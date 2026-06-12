#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_CONFIG_FILE="$SCRIPT_DIR/example-config.json"
CONFIG_FILE="$SCRIPT_DIR/config.json"
SCRAPE_SCRIPT="$SCRIPT_DIR/src/scrape.py"

if ! command -v gh &> /dev/null; then
    echo "Error: gh CLI is not installed."
    exit 1
fi

if ! gh auth status &> /dev/null; then
    echo "Error: gh CLI is not authenticated. Run 'gh auth login'."
    exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
    cp "$EXAMPLE_CONFIG_FILE" "$CONFIG_FILE"
    echo "Created $CONFIG_FILE from example-config.json. Please edit it if needed."
fi

DATA_DIR=$(python3 -c "import json, os; print(os.path.expanduser(json.load(open('$CONFIG_FILE'))['data_dir']))")
LOG_FILE=$(python3 -c "import json, os; print(os.path.expanduser(json.load(open('$CONFIG_FILE'))['log_file']))")
CRON_SCHEDULE=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['cron_schedule'])")

mkdir -p "$DATA_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

touch "$LOG_FILE"

CRON_CMD="$CRON_SCHEDULE cd $SCRIPT_DIR && /usr/bin/env python3 $SCRAPE_SCRIPT >> $LOG_FILE 2>&1"

# Remove existing entry if present
(crontab -l 2>/dev/null | grep -v "$SCRAPE_SCRIPT" || true) | crontab -

# Add new entry
(crontab -l 2>/dev/null || true; echo "$CRON_CMD") | crontab -

echo "Setup complete."
echo "Data directory: $DATA_DIR"
echo "Log file: $LOG_FILE"
echo "Cron schedule: $CRON_SCHEDULE"
echo ""
echo "Current crontab:"
crontab -l

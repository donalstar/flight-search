#!/usr/bin/env bash
# Daily flight search cron wrapper.
#
# Install with: crontab -e
# Add this line (runs daily at 6am):
#   0 6 * * * /bin/bash "/Users/donal/flight-search/run_cron.sh"

DIR="/Users/donal/flight-search"
mkdir -p "$DIR/logs"
PYTHONWARNINGS=ignore "$DIR/.venv/bin/python3" "$DIR/flight_search.py" \
    >> "$DIR/logs/run.log" 2>&1

#!/usr/bin/env bash
# Smart widget updater: only re-renders when calendar data actually changed.
# Called from cron (2x/day) or on-demand after calendar modifications.
set -euo pipefail

AUT="/home/user/workspace/automation"
cd "$AUT"
PY="$AUT/.venv/bin/python"
[ ! -x "$PY" ] && PY="$(command -v python3)"

DATA="$HOME/Obsidian/YuriVault/99_Sync/widget-data/widget_data.json"
HASH_FILE="$HOME/.cache/obsidian_widget/data_hash"

# Save old hash
OLD_HASH=""
if [ -f "$DATA" ]; then
  OLD_HASH=$(md5sum "$DATA" 2>/dev/null | cut -d' ' -f1)
fi

# Generate fresh widget_data.json from calendar
"$PY" "$AUT/generate_widget_data.py"

# Compare
NEW_HASH=$(md5sum "$DATA" 2>/dev/null | cut -d' ' -f1)

if [ "$OLD_HASH" = "$NEW_HASH" ]; then
  echo "[$(date '+%F %T')] No calendar changes detected. Skipping render."
  exit 0
fi

echo "[$(date '+%F %T')] Calendar changed! Rendering widgets..."

# Generate morning-brief style wallpaper (S24+ native, no red now-line)
TODAY=$(date +%Y-%m-%d)
"$PY" "$AUT/generate_3day_bundle_v5.py" \
  --start-date "$TODAY" --days 1 --style modern --no-now-line \
  --wallpaper 1080x2340 \
  --out "$HOME/Sync/widget-timetable/widget_wallpaper.png"

# Also render other widget profiles (square/standard/tall)
"$PY" -m obsidian_widget_renderer.cli --force
"$PY" "$AUT/obsidian_widget_renderer/export_widget_markdown.py"
echo "[$(date '+%F %T')] Widget update complete."

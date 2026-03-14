#!/usr/bin/env bash
set -euo pipefail
AUTOMATION_DIR="/home/user/workspace/automation"
PY="$AUTOMATION_DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi
OUT="$AUTOMATION_DIR/latest_timetable.png"
"$PY" "$AUTOMATION_DIR/render_html_schedule.py" \
  --date "$(date +%Y-%m-%d)" \
  --bundle \
  --v2 \
  --out "$OUT"
TS="$(date '+%Y-%m-%d %H:%M:%S')"
echo "[$TS] OK updated: $OUT"

SYNC_DIR="/home/user/notes/99_Sync/timetable"
mkdir -p "$SYNC_DIR"
cp -f "$OUT" "$SYNC_DIR/latest_timetable.png"
echo "[$TS] synced to: $SYNC_DIR/latest_timetable.png"

SYNC_DIR="/home/user/sync/widget-timetable"
mkdir -p "$SYNC_DIR"
cp -f "$OUT" "$SYNC_DIR/latest_timetable.png"
echo "[$TS] synced to: $SYNC_DIR/latest_timetable.png"

# Lock screen wallpaper (S24+ 1080x2340, no date header, no now-line)
WALLPAPER="$SYNC_DIR/widget_wallpaper.png"
"$PY" "$AUTOMATION_DIR/generate_3day_bundle_v5.py" \
  --start-date "$(date +%Y-%m-%d)" \
  --days 1 --style modern --no-now-line \
  --wallpaper 1080x2340 \
  --out "$WALLPAPER"
echo "[$TS] lock screen wallpaper updated: $WALLPAPER"

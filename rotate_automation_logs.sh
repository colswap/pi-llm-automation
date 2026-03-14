#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/home/user/workspace/automation/.logs"
ARCHIVE_DIR="$LOG_DIR/archive"
MAX_TOTAL_MB=1024   # 1GB total cap for current + archive logs
KEEP_DAYS=30

mkdir -p "$LOG_DIR" "$ARCHIVE_DIR"

# 1) rotate any log >20MB
while IFS= read -r -d '' file; do
  base="$(basename "$file")"
  ts="$(date +%Y%m%d-%H%M%S)"
  gz="$ARCHIVE_DIR/${base}.${ts}.gz"
  gzip -c "$file" > "$gz"
  : > "$file"
done < <(find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -size +20M -print0)

# 2) remove archives older than KEEP_DAYS
find "$ARCHIVE_DIR" -type f -name '*.gz' -mtime +"$KEEP_DAYS" -delete

# 3) enforce total cap by deleting oldest archives first
max_bytes=$((MAX_TOTAL_MB * 1024 * 1024))
current_bytes=$(du -sb "$LOG_DIR" | awk '{print $1}')
if (( current_bytes > max_bytes )); then
  while (( current_bytes > max_bytes )); do
    oldest=$(find "$ARCHIVE_DIR" -type f -name '*.gz' -printf '%T@ %p\n' | sort -n | head -n1 | cut -d' ' -f2-)
    [[ -z "${oldest:-}" ]] && break
    rm -f "$oldest"
    current_bytes=$(du -sb "$LOG_DIR" | awk '{print $1}')
  done
fi

echo "[$(date '+%F %T')] rotate done: total=$(du -sh "$LOG_DIR" | awk '{print $1}')" >> "$LOG_DIR/logrotate-local.log"

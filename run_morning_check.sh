#!/usr/bin/env bash
set -euo pipefail

source /home/user/.profile >/dev/null 2>&1 || true
source /home/user/.bashrc >/dev/null 2>&1 || true
export PATH="/home/user/.npm-global/bin:/home/user/.local/bin:$PATH"

WORKDIR="/home/user/workspace"
HEALTH="$WORKDIR/automation/check_snu_automation_health.sh"
FX="$WORKDIR/automation/fx_usdkrw_check.py"
BACKUP="$WORKDIR/automation/backup_snu_state.sh"

# 1) automation health check first
bash "$HEALTH"

# 2) USD/KRW signal (조건 충족 시에만 발송)
FX_OUT="$(python "$FX")"
if [[ "$FX_OUT" != "NO_ALERT" ]]; then
  agent-cli message send --channel telegram --account alerts --target TELEGRAM_CHAT_ID --message "$FX_OUT" >/dev/null
fi

# 3) scholarship — 09:15 시스템 crontab(run_scholarship_pipeline_daily.sh)으로 통합됨.
#    이중 실행 방지를 위해 여기서는 제거. (2026-03-11)

# 4) backup state
bash "$BACKUP"

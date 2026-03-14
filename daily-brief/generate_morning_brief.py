#!/usr/bin/env python3
"""Daily Morning Brief generator.

Collects status from all automation components, aggregates failure logs,
and produces a human-readable brief (text) + structured JSON output.

Usage:
    python generate_morning_brief.py --dry-run     # stdout only, no telegram
    python generate_morning_brief.py               # generate + send
    python generate_morning_brief.py --force        # resend even if already sent today
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def atomic_write(path: Path, content: str):
    """Write to a temp file then rename for crash-safe writes."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.rename(path)

# ── paths ──────────────────────────────────────────────────────────────
AUTOMATION_DIR = Path(__file__).resolve().parent.parent  # automation/
STATE_DIR = AUTOMATION_DIR / ".state"
LOGS_DIR = AUTOMATION_DIR / ".logs"
BRIEF_OUTPUT_DIR = AUTOMATION_DIR / "daily-brief" / "output"

# ── severity ───────────────────────────────────────────────────────────
SEVERITY_ORDER = {"CRITICAL": 0, "ERROR": 1, "WARN": 2, "INFO": 3}

# ── data classes ───────────────────────────────────────────────────────
@dataclass
class FailureEntry:
    component: str
    error_type: str
    target: str
    severity: str
    first_seen: str
    last_seen: str
    count: int = 1
    resolved: bool = False
    resolved_at: Optional[str] = None
    next_action: str = ""

    @property
    def group_key(self):
        return (self.component, self.error_type, self.target)


@dataclass
class SourceResult:
    source_id: str
    status: str  # OK, WARN, FAIL, STALE, UNKNOWN
    detail: str = ""
    errors: list = field(default_factory=list)


# ── timestamp helpers ──────────────────────────────────────────────────
TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
NOW = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")
YESTERDAY = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
STALE_HOURS = 49 if NOW.weekday() in (5, 6) else 25  # weekend: 49h


def parse_ts(s: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def is_stale(ts_str: str) -> bool:
    ts = parse_ts(ts_str)
    if ts is None:
        return True
    return (NOW - ts).total_seconds() > STALE_HOURS * 3600


def ts_now() -> str:
    return NOW.strftime("%Y-%m-%dT%H:%M:%S")


# ── S1: scholarship digest error log ──────────────────────────────────
def parse_s1() -> SourceResult:
    path = LOGS_DIR / "snu_scholarship_digest.err.log"
    if not path.exists():
        return SourceResult("S1", "UNKNOWN", "err.log not found")
    # Read only last 50KB to avoid unbounded memory usage
    MAX_TAIL = 50 * 1024
    fsize = path.stat().st_size
    if fsize > MAX_TAIL:
        with path.open("r", errors="replace") as fh:
            fh.seek(fsize - MAX_TAIL)
            fh.readline()  # skip partial line
            text = fh.read()
    else:
        text = path.read_text(errors="replace")
    entries = []
    blocks = re.split(r"(?=\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ERROR)", text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        m = TS_RE.search(block)
        if not m:
            continue
        ts = m.group(1)
        error_type = "UnknownError"
        target = "unknown"
        if "ConnectionResetError" in block:
            error_type = "ConnectionResetError"
        elif "ConnectionError" in block:
            error_type = "ConnectionError"
        elif "Timeout" in block:
            error_type = "TimeoutError"
        for host in ["cse.snu.ac.kr", "eng.snu.ac.kr"]:
            if host in block:
                target = host
                break
        if "fetch(CSE_LIST)" in block:
            target = "cse.snu.ac.kr"
        elif "fetch(ENG_LIST)" in block:
            target = "eng.snu.ac.kr"
        entries.append(FailureEntry(
            component="snu_scholarship_digest",
            error_type=error_type,
            target=target,
            severity="ERROR",
            first_seen=ts, last_seen=ts,
        ))
    return SourceResult("S1", "OK" if not entries else "FAIL",
                        f"{len(entries)} error(s) in err.log", entries)


# ── S2: scholarship digest success log ────────────────────────────────
def parse_s2() -> SourceResult:
    path = LOGS_DIR / "snu_scholarship_digest.log"
    if not path.exists():
        return SourceResult("S2", "UNKNOWN", "log not found")
    lines = path.read_text().strip().splitlines()
    if not lines:
        return SourceResult("S2", "WARN", "log empty")
    last = lines[-1]
    m = TS_RE.search(last)
    ts = m.group(1) if m else "unknown"
    if "SENT" in last:
        return SourceResult("S2", "OK", f"SENT ({ts})")
    elif "NO_UPDATES" in last:
        return SourceResult("S2", "OK", f"NO_UPDATES ({ts})")
    return SourceResult("S2", "WARN", f"last: {last.strip()}")


# ── S3: health state ──────────────────────────────────────────────────
def parse_s3() -> SourceResult:
    path = STATE_DIR / "health_state.json"
    if not path.exists():
        return SourceResult("S3", "UNKNOWN", "health_state.json not found")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return SourceResult("S3", "STALE", "JSON parse error")
    fails = data.get("consecutive_fails", 0)
    if fails == 0:
        return SourceResult("S3", "OK", "consecutive_fails=0")
    elif fails <= 2:
        return SourceResult("S3", "WARN", f"consecutive_fails={fails}")
    else:
        return SourceResult("S3", "FAIL", f"consecutive_fails={fails}",
                            [FailureEntry("health_check", "ConsecutiveFailure",
                                          "automation_health", "ERROR",
                                          TODAY, TODAY, fails)])


# ── S4: FX state ──────────────────────────────────────────────────────
def parse_s4() -> SourceResult:
    path = STATE_DIR / "usdkrw_state.json"
    if not path.exists():
        return SourceResult("S4", "UNKNOWN", "usdkrw_state.json not found")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return SourceResult("S4", "STALE", "JSON parse error")
    price = data.get("last_price", "?")
    updated = data.get("updated_at", "")
    if is_stale(updated.replace("T", " ")):
        return SourceResult("S4", "STALE", f"{price} (stale: {updated})")
    return SourceResult("S4", "OK", f"NO_ALERT, {price:,.2f}" if isinstance(price, (int, float)) else f"NO_ALERT, {price}")


# ── S5: backup log ────────────────────────────────────────────────────
def parse_s5() -> SourceResult:
    path = LOGS_DIR / "backup-state.log"
    if not path.exists():
        return SourceResult("S5", "UNKNOWN", "backup-state.log not found")
    lines = path.read_text().strip().splitlines()
    if not lines:
        return SourceResult("S5", "WARN", "log empty")
    last = lines[-1]
    m = TS_RE.search(last)
    ts = m.group(1) if m else "unknown"
    if is_stale(ts):
        return SourceResult("S5", "STALE", f"last backup: {ts}")
    return SourceResult("S5", "OK", ts)


# ── S6: log rotation ──────────────────────────────────────────────────
def parse_s6() -> SourceResult:
    path = LOGS_DIR / "logrotate-local.log"
    if not path.exists():
        return SourceResult("S6", "UNKNOWN", "logrotate-local.log not found")
    lines = path.read_text().strip().splitlines()
    if not lines:
        return SourceResult("S6", "WARN", "log empty")
    last = lines[-1]
    m = TS_RE.search(last)
    ts = m.group(1) if m else "unknown"
    if is_stale(ts):
        return SourceResult("S6", "STALE", f"last rotate: {ts}")
    return SourceResult("S6", "OK", ts)


# ── S9: schedule (optional) ───────────────────────────────────────────
def parse_s9() -> SourceResult:
    builder = AUTOMATION_DIR / "schedule_prompt_builder.py"
    if not builder.exists():
        return SourceResult("S9", "UNKNOWN", "schedule_prompt_builder.py not found")
    try:
        out = subprocess.run(
            [sys.executable, str(builder), "--mode", "day", "--date", TODAY],
            capture_output=True, text=True, timeout=15
        )
        if out.returncode == 0 and out.stdout.strip():
            return SourceResult("S9", "OK", out.stdout.strip()[:300])
        # Distinguish known-down (token expiry) from unexpected failures
        stderr_lower = (out.stderr or "").lower()
        if any(kw in stderr_lower for kw in ("keyunwrap", "token", "integrity check")):
            return SourceResult("S9", "KNOWN_DOWN", "Google Calendar token expired")
        return SourceResult("S9", "WARN", f"exit={out.returncode}")
    except Exception as e:
        return SourceResult("S9", "WARN", f"schedule fetch failed: {e}")


def build_3day_schedule_block() -> str:
    cli = AUTOMATION_DIR / "timetable_cli.py"
    start = NOW.date()
    end = start + timedelta(days=2)
    lines = [f"[모닝 브리프 | {start.strftime('%m-%d')}~{end.strftime('%m-%d')}]", ""]

    for i in range(3):
        d = start + timedelta(days=i)
        day_iso = d.isoformat()
        lines.append(f"[Day{i+1} {d.strftime('%m-%d')} {DAY_KO[d.weekday()]}]")
        try:
            out = subprocess.run(
                [sys.executable, str(cli), "show", "--date", day_iso],
                capture_output=True, text=True, timeout=20
            )
            if out.returncode != 0:
                lines.append("• (일정 불러오기 실패)")
                lines.append("")
                continue
            item_lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().startswith("-")]
            if not item_lines:
                lines.append("• (일정 없음)")
            else:
                for ln in item_lines[:12]:
                    lines.append(f"• {ln[2:]}")
        except Exception:
            lines.append("• (일정 불러오기 실패)")
        lines.append("")

    return "\n".join(lines).strip()


# ── S10: system metrics ───────────────────────────────────────────────
def parse_s10() -> SourceResult:
    try:
        df_out = subprocess.run(["df", "-h", "/mnt/usb32"], capture_output=True, text=True, timeout=5)
        uptime_out = subprocess.run(["uptime", "-p"], capture_output=True, text=True, timeout=5)
        df_lines = df_out.stdout.strip().splitlines()
        disk_info = ""
        if len(df_lines) >= 2:
            parts = df_lines[1].split()
            if len(parts) >= 5:
                disk_info = f"{parts[2]}/{parts[1]} ({parts[4]})"
        uptime_str = uptime_out.stdout.strip()
        detail = f"disk: {disk_info}, {uptime_str}"
        pct = int(parts[4].replace("%", "")) if disk_info and len(parts) >= 5 else 0
        status = "WARN" if pct > 80 else "OK"
        return SourceResult("S10", status, detail)
    except Exception as e:
        return SourceResult("S10", "UNKNOWN", str(e))


# ── failure aggregation ───────────────────────────────────────────────
def aggregate_failures(all_errors: list[FailureEntry]) -> list[FailureEntry]:
    groups: dict[tuple, FailureEntry] = {}
    for e in all_errors:
        key = e.group_key
        if key in groups:
            g = groups[key]
            g.count += 1
            if e.first_seen < g.first_seen:
                g.first_seen = e.first_seen
            if e.last_seen > g.last_seen:
                g.last_seen = e.last_seen
            if SEVERITY_ORDER.get(e.severity, 9) < SEVERITY_ORDER.get(g.severity, 9):
                g.severity = e.severity
        else:
            groups[key] = FailureEntry(
                component=e.component, error_type=e.error_type, target=e.target,
                severity=e.severity, first_seen=e.first_seen, last_seen=e.last_seen,
                count=1,
            )
    return sorted(groups.values(), key=lambda f: SEVERITY_ORDER.get(f.severity, 9))


def check_resolved(failures: list[FailureEntry], s2_result: SourceResult) -> list[FailureEntry]:
    """Mark failures as resolved if we have evidence of recovery."""
    for f in failures:
        if f.component == "snu_scholarship_digest" and s2_result.status == "OK":
            last_fail = parse_ts(f.last_seen)
            if "SENT" in s2_result.detail:
                m = re.search(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)", s2_result.detail)
                if m:
                    sent_ts = parse_ts(m.group(1))
                    if sent_ts and last_fail and sent_ts > last_fail:
                        f.resolved = True
                        f.resolved_at = m.group(1)
                        f.next_action = "auto-resolved"
    return failures


# ── load/save failure tracker for resolved tracking ────────────────────
TRACKER_PATH = STATE_DIR / "failure_tracker.json"

def load_tracker() -> dict:
    if TRACKER_PATH.exists():
        try:
            return json.loads(TRACKER_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"failures": [], "last_updated": ""}


def save_tracker(failures: list[FailureEntry]):
    data = {
        "failures": [asdict(f) for f in failures],
        "last_updated": ts_now(),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(TRACKER_PATH, json.dumps(data, ensure_ascii=False, indent=2))


# ── status emoji ───────────────────────────────────────────────────────
STATUS_ICON = {"OK": "v", "WARN": "!", "FAIL": "X", "STALE": "?", "UNKNOWN": "-"}


def status_line(label: str, result: SourceResult) -> str:
    icon = STATUS_ICON.get(result.status, "?")
    return f"[{icon}] {label}: {result.detail}"


# ── render text brief ─────────────────────────────────────────────────
WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]
DAY_KO = WEEKDAYS_KR

def render_brief(sources: dict[str, SourceResult], failures: list[FailureEntry]) -> str:
    wd = WEEKDAYS_KR[NOW.weekday()]
    date_str = NOW.strftime(f"%Y-%m-%d {wd}")
    lines = [f"Morning Brief ({date_str})", ""]

    # TL;DR — S1 (err.log) is historical; S2 (success log) is the definitive status
    statuses = [s.status for s in sources.values() if s.source_id in ("S2","S3","S4","S5","S6")]
    has_fail = any(s == "FAIL" for s in statuses)
    has_warn = any(s in ("WARN", "STALE") for s in statuses)
    unresolved = [f for f in failures if not f.resolved]
    resolved = [f for f in failures if f.resolved]

    if has_fail:
        overall = "FAIL"
    elif has_warn or unresolved:
        overall = "WARN"
    else:
        overall = "OK"

    parts = [f"system: {overall}"]
    if unresolved:
        parts.append(f"{len(unresolved)} unresolved failure(s)")
    if resolved:
        parts.append(f"{len(resolved)} resolved")
    # missing sources
    missing = [s for s in sources.values() if s.status == "UNKNOWN" and s.source_id in ("S1","S2","S3","S4","S5")]
    if missing:
        parts.append(f"source(s) missing: {','.join(s.source_id for s in missing)}")
    lines.append("[TL;DR]")
    lines.append(" | ".join(parts))
    lines.append("")

    # Schedule (3-day fixed format)
    lines.append(build_3day_schedule_block())
    lines.append("")

    # System status
    lines.append("--- system status ---")
    labels = [
        ("S2", "scholarship digest"),
        ("S4", "USD/KRW"),
        ("S5", "backup"),
        ("S6", "log rotation"),
        ("S3", "health check"),
    ]
    for sid, label in labels:
        if sid in sources:
            lines.append(status_line(label, sources[sid]))
    # system metrics
    s10 = sources.get("S10")
    if s10:
        lines.append(status_line("system", s10))
    lines.append("")

    # Failures
    lines.append("--- failure log ---")
    if not failures:
        lines.append("  no failures in recent logs")
    else:
        for f in failures:
            tag = "[RESOLVED] " if f.resolved else ""
            lines.append(f"  {tag}{f.component} / {f.error_type}")
            lines.append(f"    target: {f.target}")
            lines.append(f"    period: {f.first_seen} ~ {f.last_seen} ({f.count}x)")
            lines.append(f"    severity: {f.severity}")
            if f.resolved:
                lines.append(f"    resolved: {f.resolved_at}")
            if f.next_action:
                lines.append(f"    action: {f.next_action}")
    lines.append("")

    # Risks
    lines.append("--- risks ---")
    risks = []
    if s10 and "WARN" == s10.status:
        risks.append(f"disk usage high: {s10.detail}")
    for f in failures:
        if f.resolved:
            risks.append(f"{f.target} had instability ({f.first_seen}~{f.last_seen}) - monitor for recurrence")
    if sources.get("S3") and sources["S3"].status in ("WARN", "FAIL"):
        risks.append(f"health check: {sources['S3'].detail}")
    if not risks:
        lines.append("  none")
    else:
        for r in risks:
            lines.append(f"  - {r}")
    lines.append("")

    # Action items
    lines.append("--- action items ---")
    actions = []
    for f in failures:
        if not f.resolved:
            actions.append(f"investigate {f.component}/{f.error_type} on {f.target}")
    if not actions:
        lines.append("  none")
    else:
        for i, a in enumerate(actions, 1):
            lines.append(f"  {i}. {a}")

    text = "\n".join(lines)
    # Completeness safeguard: ensure required sections exist
    for section in ["[TL;DR]", "--- system status ---", "--- failure log ---"]:
        if section not in text:
            text += f"\n[WARN] missing section: {section}"
    if len(text) < 50:
        text = f"Morning Brief ({date_str})\n\n[ERROR] Brief generation incomplete. Check logs."
    return text


# ── build JSON output ─────────────────────────────────────────────────
def build_json(sources: dict[str, SourceResult], failures: list[FailureEntry], brief_text: str) -> dict:
    # extract TL;DR from text
    tldr = ""
    for line in brief_text.splitlines():
        if line and not line.startswith("[") and not line.startswith("---") and not line.startswith("Morning"):
            tldr = line.strip()
            break
    return {
        "date": TODAY,
        "generated_at": ts_now(),
        "tldr": tldr,
        "system_status": {
            s.source_id: {"status": s.status, "detail": s.detail}
            for s in sources.values()
        },
        "failures": [asdict(f) for f in failures],
        "brief_text": brief_text,
    }


# ── sent tracking ─────────────────────────────────────────────────────
SENT_DATES_PATH = STATE_DIR / "morning_brief_sent_dates.json"

def already_sent_today() -> bool:
    if SENT_DATES_PATH.exists():
        try:
            data = json.loads(SENT_DATES_PATH.read_text())
            return TODAY in data.get("dates", [])
        except json.JSONDecodeError:
            pass
    return False


def mark_sent():
    data = {"dates": []}
    if SENT_DATES_PATH.exists():
        try:
            data = json.loads(SENT_DATES_PATH.read_text())
        except json.JSONDecodeError:
            pass
    dates = data.get("dates", [])
    if TODAY not in dates:
        dates.append(TODAY)
    # keep last 14 days only
    cutoff = (NOW - timedelta(days=14)).strftime("%Y-%m-%d")
    dates = [d for d in dates if d >= cutoff]
    data["dates"] = dates
    atomic_write(SENT_DATES_PATH, json.dumps(data, indent=2))


# ── telegram send ─────────────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    try:
        result = subprocess.run(
            ["agent-framework", "message", "send",
             "--channel", "telegram",
             "--account", "alerts",
             "--target", "TELEGRAM_CHAT_ID",
             "--message", text],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[WARN] telegram send failed: {e}", file=sys.stderr)
        return False


# ── main ───────────────────────────────────────────────────────────────
LOCK_PATH = STATE_DIR / "morning_brief.lock"


def acquire_lock():
    """Acquire exclusive lock to prevent concurrent runs."""
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except BlockingIOError:
        print("[INFO] Another instance running. Exiting.")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Generate Daily Morning Brief")
    parser.add_argument("--dry-run", action="store_true", help="Print brief to stdout, skip telegram")
    parser.add_argument("--force", action="store_true", help="Send even if already sent today")
    parser.add_argument("--json-only", action="store_true", help="Only output JSON to stdout")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = acquire_lock()  # noqa: F841 — held for process lifetime

    # H4: retry previously unsent brief
    unsent_path = STATE_DIR / "morning_brief_unsent.json"
    if unsent_path.exists() and not args.dry_run:
        try:
            unsent = json.loads(unsent_path.read_text())
            if send_telegram(unsent.get("brief_text", "")):
                unsent_path.unlink()
                print(f"[OK] Resent previously unsent brief")
        except Exception as e:
            print(f"[WARN] unsent retry failed: {e}", file=sys.stderr)

    # collect all sources (isolated: one parser crash won't kill the brief)
    sources: dict[str, SourceResult] = {}
    parsers = [parse_s1, parse_s2, parse_s3, parse_s4, parse_s5, parse_s6, parse_s9, parse_s10]
    for fn in parsers:
        try:
            r = fn()
        except Exception as e:
            sid = fn.__name__.replace("parse_", "").upper()
            r = SourceResult(sid, "FAIL", f"parser crash: {e}")
        sources[r.source_id] = r

    # aggregate failures
    all_errors = []
    for s in sources.values():
        all_errors.extend(s.errors)
    failures = aggregate_failures(all_errors)

    # check resolved against success evidence
    s2 = sources.get("S2", SourceResult("S2", "UNKNOWN"))
    failures = check_resolved(failures, s2)

    # merge with tracker for historical resolved status
    prev = load_tracker()
    prev_map = {}
    for pf in prev.get("failures", []):
        key = (pf.get("component"), pf.get("error_type"), pf.get("target"))
        prev_map[key] = pf
    for f in failures:
        pk = f.group_key
        if pk in prev_map and prev_map[pk].get("resolved"):
            f.resolved = True
            f.resolved_at = f.resolved_at or prev_map[pk].get("resolved_at", "")
            f.next_action = f.next_action or prev_map[pk].get("next_action", "")

    # filter out resolved failures older than 3 days
    cutoff_3d = (NOW - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    failures = [f for f in failures if not f.resolved or f.last_seen >= cutoff_3d]

    # render
    brief_text = render_brief(sources, failures)
    brief_json = build_json(sources, failures, brief_text)

    if args.json_only:
        print(json.dumps(brief_json, ensure_ascii=False, indent=2))
        return 0

    # save outputs
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BRIEF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = STATE_DIR / "morning_brief_latest.json"
    atomic_write(json_path, json.dumps(brief_json, ensure_ascii=False, indent=2))

    text_path = BRIEF_OUTPUT_DIR / f"brief-{TODAY}.txt"
    atomic_write(text_path, brief_text)

    save_tracker(failures)

    if args.dry_run:
        print(brief_text)
        print(f"\n[dry-run] JSON saved to: {json_path}")
        print(f"[dry-run] Text saved to: {text_path}")
        return 0

    # send
    if already_sent_today() and not args.force:
        print(f"[INFO] Already sent today ({TODAY}). Use --force to resend.")
        print(brief_text)
        return 0

    if send_telegram(brief_text):
        mark_sent()
        print(f"[OK] Brief sent for {TODAY}")
    else:
        unsent_path = STATE_DIR / "morning_brief_unsent.json"
        atomic_write(unsent_path, json.dumps(brief_json, ensure_ascii=False, indent=2))
        print(f"[WARN] Send failed. Saved to {unsent_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

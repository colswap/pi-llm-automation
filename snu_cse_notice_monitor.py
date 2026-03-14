#!/usr/bin/env python3
"""SNU CSE 공지사항 모니터 — 새 공지 감지 시 텔레그램 알림"""

import json
import re
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

import requests

NOTICE_URL = "https://cse.snu.ac.kr/community/notice"
STATE_FILE = Path(__file__).parent / "snu_cse_notice_state.json"

def fetch_notices():
    """SSR 스트림에서 공지 목록 추출"""
    resp = requests.get(NOTICE_URL, timeout=15)
    resp.raise_for_status()
    html = resp.text
    
    # Extract the streamController.enqueue JSON payload
    m = re.search(r'streamController\.enqueue\("(.+?)"\)', html, re.DOTALL)
    if not m:
        print("ERROR: Could not find SSR stream data", file=sys.stderr)
        sys.exit(1)
    
    raw = m.group(1).replace('\\"', '"').replace('\\n', '\n')
    data = json.loads(raw)
    
    # Parse the flat array format — notices are objects with keys like _15, _17, _19...
    notices = []
    for item in data:
        if isinstance(item, dict) and "_15" in item and "_17" in item:
            # Resolve references: _15→id, _17→title, _19→createdAt, _21→isPinned, _23→hasAttachment
            notice_id = data[item["_15"]]
            title = data[item["_17"]]
            created_at = data[item["_19"]]
            is_pinned = data[item["_21"]]
            
            notices.append({
                "id": notice_id,
                "title": title,
                "created_at": created_at,
                "is_pinned": is_pinned,
                "url": f"https://cse.snu.ac.kr/community/notice/{notice_id}"
            })
    
    return notices


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen_ids": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def main():
    notices = fetch_notices()
    if not notices:
        print("NO_UPDATES")
        return
    
    state = load_state()
    seen = set(state.get("seen_ids", []))
    
    new_notices = [n for n in notices if n["id"] not in seen]
    
    if not new_notices:
        print("NO_UPDATES")
        return
    
    # Build alert message
    lines = ["📢 **SNU CSE 새 공지사항**\n"]
    for n in new_notices[:10]:  # max 10
        pin = "📌 " if n["is_pinned"] else ""
        dt = n["created_at"][:10]
        lines.append(f"• {pin}[{n['title']}]({n['url']}) ({dt})")
    
    print("\n".join(lines))
    
    # Update state — keep last 200 IDs
    all_ids = list(seen | {n["id"] for n in notices})
    state["seen_ids"] = all_ids[-200:]
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


if __name__ == "__main__":
    main()

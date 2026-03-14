# 🍓 Raspberry Pi LLM Daily Automation

Raspberry Pi 4 위에서 LLM 에이전트를 24시간 운영하며, 일상 반복 작업을 자동화하는 개인 시스템.

## 주요 파이프라인

### 📬 이메일 자동화
- Gmail API로 여러 계정 미읽은 메일 스캔
- LLM 기반 중요도 분류 → 텔레그램 요약 리포트 발송

### 📅 캘린더 + 시간표
- Google Calendar API + 학교 시간표(JSON) 병합
- 당일 스케줄 이미지 자동 렌더링 (Python, Pillow)
- 모닝 브리핑 자동 생성 (일정 + 메일 요약 + 마감 알림)
- 주간 리뷰 + 7일 타임테이블 이미지

### 📱 모바일 잠금화면 자동화
- 시간표 이미지 렌더링 → Syncthing 동기화 → Tasker 파일 변경 감지 → 잠금화면 자동 교체
- 캘린더 변경 시 재렌더링 → 실시간 반영

### 📋 메모 자동 분류
- 텔레그램 메모 입력 → 과목별 자동 분류 → Obsidian 노트 저장
- 과제/마감 감지 시 Google Calendar 자동 등록

### 🎓 장학금 크롤링
- 대학 장학 페이지 스크래핑 → 자격조건 자동 필터링 → 신규 공고 알림

### 🔔 알림 시스템
- 마감 D-3 자동 알림 (cron 스케줄)
- 학과 공지 모니터링 (6시간 주기)

## 기술 스택
- **Language:** Python
- **APIs:** Google Calendar API, Gmail API
- **Infra:** Raspberry Pi 4 (arm64), Tailscale VPN, Syncthing
- **Mobile:** Android Tasker
- **Notes:** Obsidian (Markdown)
- **LLM:** Claude API (via agent framework)

## 보안
- API 인증 정보: `.env` 파일 분리, 버전관리 제외
- 원격 접근: Tailscale VPN (WireGuard 기반) — 공개 포트 없음
- SSH: 키 기반 인증만 허용

## 구조
```
├── generate_widget_data.py       # 캘린더 → 위젯 데이터 JSON 생성
├── generate_3day_bundle_v5.py    # 1~3일 타임테이블 이미지 렌더링
├── generate_week_agenda_v5.py    # 주간 어젠다 이미지
├── render_html_schedule.py       # HTML 기반 시간표 렌더링
├── scholarship_pipeline_v4.py    # 장학금 크롤링 + LLM 필터링
├── snu_cse_notice_monitor.py     # 학과 공지 모니터링
├── timetable_cli.py              # 시간표 관리 CLI
├── update_widget_if_changed.sh   # 캘린더 변경 감지 → 위젯 갱신
├── update_latest_timetable.sh    # 일일 시간표 업데이트
├── run_morning_check.sh          # 모닝 체크 스크립트
├── daily-brief/                  # 모닝 브리핑 생성기
└── obsidian_widget_renderer/     # Obsidian 위젯 렌더링 모듈
```

## 참고
- 개인 정보(이메일, 경로 등)는 플레이스홀더로 치환되어 있습니다.
- 실제 운영 환경의 `.env` 파일은 포함되지 않습니다.

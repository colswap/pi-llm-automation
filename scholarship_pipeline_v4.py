#!/usr/bin/env python3
"""Scholarship Pipeline v4 — PDF/OCR/HWP/DOCX/external-link enrichment + LLM extraction."""
import argparse
import hashlib
import io
import json
import os
import re
import signal
import struct
import tempfile
import time
import zlib
import zipfile
from dataclasses import dataclass, asdict, field
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENG_LIST = "https://eng.snu.ac.kr/snu/bbs/BMSR00004/list.do?menuNo=200176"
CSE_LIST = "https://cse.snu.ac.kr/community/notice"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (OpenClaw Scholarship Pipeline v4)",
    "Accept-Language": "ko,en;q=0.8",
}
INCLUDE_KEYWORDS = ["장학", "학자금", "등록금", "대출이자", "지원금"]
EXCLUDE_IN_TITLE = ["세미나", "컨퍼런스", "채용", "공모전", "연구윤리", "오픈액세스"]

VAULT = Path('/home/user/notes')
BASE = VAULT / '20_Projects' / 'Scholarship'
RAW_DIR = BASE / 'raw'
QUEUE_DIR = BASE / 'approval_queue'
APPROVED_DIR = BASE / 'approved'
STATE_DIR = BASE / 'state'
ALERT_QUEUE = BASE / 'alerts' / 'pending.md'
CAL_QUEUE = BASE / 'calendar' / 'pending.md'

# Attachment limits
PDF_MAX_SIZE = 5 * 1024 * 1024       # 5 MB
PDF_MAX_PER_NOTICE = 3
IMG_MAX_SIZE = 2 * 1024 * 1024       # 2 MB
IMG_MAX_PER_NOTICE = 5
ATTACHMENT_DOWNLOAD_TIMEOUT = 30      # seconds
ATTACHMENT_TOTAL_TIMEOUT = 120        # seconds per notice
EXTERNAL_LINK_MAX = 2

EXTERNAL_LINK_DOMAINS = [
    "kosaf.go.kr", "scholarship.kra.co.kr", "gov.kr",
    "dreamsponsor.or.kr", "bokjiro.go.kr",
]

# ---------------------------------------------------------------------------
# Optional imports (graceful fallback)
# ---------------------------------------------------------------------------
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import olefile
except ImportError:
    olefile = None

try:
    import docx as python_docx
except ImportError:
    python_docx = None

try:
    from PIL import Image
    import subprocess as _sp
    # check tesseract availability
    _sp.run(["tesseract", "--version"], capture_output=True, check=True)
    HAS_TESSERACT = True
except Exception:
    HAS_TESSERACT = False
    Image = None


# ---------------------------------------------------------------------------
# LLM extraction (OpenRouter — Gemini 2.5 Flash)
# ---------------------------------------------------------------------------
_OPENROUTER_KEY: Optional[str] = os.environ.get("OPENROUTER_API_KEY")
_LLM_MODEL = "google/gemini-2.5-flash"
_LLM_TIMEOUT = 30  # seconds
_LLM_MAX_INPUT = 6000  # chars of body text to send
_LLM_RETRY = 2
_LLM_CALL_DELAY = 0.5  # seconds between calls (rate limit courtesy)

# Try loading key from OpenClaw auth-profiles if env not set
if not _OPENROUTER_KEY:
    _auth_paths = [
        Path("/mnt/usb32/agent-data/agents/main/agent/auth-profiles.json"),
        Path.home() / ".agent-config" / "agents" / "main" / "agent" / "auth-profiles.json",
    ]
    for _ap in _auth_paths:
        if _ap.exists():
            try:
                _ad = json.loads(_ap.read_text())
                _profiles = _ad.get("profiles", _ad) if isinstance(_ad, dict) else _ad[0].get("profiles", {})
                _ork = _profiles.get("openrouter:manual", {})
                _OPENROUTER_KEY = _ork.get("token") or _ork.get("apiKey")
                if _OPENROUTER_KEY:
                    break
            except Exception:
                pass

_USER_PROFILE = {
    "university": "서울대학교",
    "department": "컴퓨터공학부",
    "degree": "학부",
    "year": 3,
    "semester": "2026-1학기 (5번째 학기)",
    "income_bracket": 9,
    "nationality": "대한민국",
    "residence": "서울",
    "notes": "비장애인, 비다문화가정, 비고졸후학습자, 비자립준비청년",
}

_LLM_EXTRACT_PROMPT = """\
당신은 장학금 공지 분석 전문가입니다. 아래 장학금 공지 원문을 읽고, 다음 필드를 정확히 추출하세요.

## 추출 필드
1. **summary**: 이 장학금이 뭔지 2~3문장으로 요약 (누가, 얼마, 어떤 조건). 불필요한 행정 문구 제거.
2. **deadline**: 신청/제출 마감일. "YYYY.M.D" 형식. 여러 날짜가 있으면 최종 마감일. 없으면 "미정".
3. **income_condition**: 소득분위/소득구간 조건. 예: "8분위 이하", "기초생활수급자~4분위", "제한 없음". 원문에 소득 관련 언급이 없으면 "제한 없음".
4. **amount**: 장학금액. 예: "등록금 전액", "월 50만원 (12개월)", "300만원". 없으면 "미정".
5. **target**: 지원 대상. 예: "학부 3학년 이상", "대학원 석·박사", "전체 재학생". 구체적으로.
6. **relevant**: 아래 사용자 프로필 기준으로 이 장학금에 지원 가능한지 판단. true/false.
7. **relevance_reason**: relevant가 false인 경우 사유를 한 줄로. (예: "대학원생 전용", "소득 3구간 이하만 해당", "통영시 거주자 한정")

## 사용자 프로필
- 대학: {university} {department}
- 과정: {degree} {year}학년 ({semester})
- 소득구간: {income_bracket}구간
- 국적: {nationality}, 거주지: {residence}
- 기타: {notes}

## 규칙
- 원문에 명시된 정보만 추출. 추측하지 마세요.
- 소득분위 조건이 선발 기준(점수 합산용)으로만 언급되고 자격 제한이 아니면 "제한 없음 (선발 시 소득구간 반영)"으로 적으세요.
- relevant 판단: 장학금이 아닌 공지, 대학원 전용, 소득구간 미충족, 지역 제한, 특수 자격(장애, 고졸후학습 등) → false.  애매하면 true로.
- JSON만 출력. 다른 텍스트 없이.

## 원문
{body}

## 출력 (JSON)
"""


def _llm_extract_fields(body_text: str, title: str = "") -> Optional[dict]:
    """Call LLM to extract structured fields from notice body. Returns dict or None on failure."""
    if not _OPENROUTER_KEY:
        return None

    truncated = body_text[:_LLM_MAX_INPUT]
    if title:
        truncated = f"[제목] {title}\n\n{truncated}"

    prompt = _LLM_EXTRACT_PROMPT.format(body=truncated, **_USER_PROFILE)

    payload = {
        "model": _LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {_OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://agent-framework.ai",
        "X-Title": "Scholarship Pipeline",
    }

    for attempt in range(_LLM_RETRY):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=_LLM_TIMEOUT,
            )
            if resp.status_code == 429:
                # Rate limited — wait and retry
                wait = float(resp.headers.get("Retry-After", 5))
                time.sleep(min(wait, 15))
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            result = json.loads(content)
            # Validate expected keys
            for key in ("summary", "deadline", "income_condition", "amount", "target"):
                if key not in result:
                    result[key] = ""
            if "relevant" not in result:
                result["relevant"] = True
            if "relevance_reason" not in result:
                result["relevance_reason"] = ""
            return result
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
            if attempt < _LLM_RETRY - 1:
                time.sleep(2)
                continue
            return None
    return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Notice:
    source: str
    title: str
    url: str
    posted: str
    detail_text: str
    attachments: List[str]
    deadline: str
    income_condition: str
    summary: str
    content_hash: str
    dedupe_key: str
    merged_sources: List[str]
    # v4 new fields
    external_links: List[str] = field(default_factory=list)
    amount: str = ""
    target: str = ""
    # v4.1 LLM relevance
    relevant: bool = True
    relevance_reason: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def extract_content_area(html: str) -> str:
    """Try to extract only the main content area from HTML, stripping nav/sidebar."""
    # SNU ENG site: content is specifically in board_cont / board_view
    for pat in [
        # SNU ENG: strict .board_cont → first div after (| </article>)
        r'<div[^>]*class="[^"]*board_cont[^"]*"[^>]*>([\s\S]*?)<(div[^>]*class|/article|</div>\s*<div[^>]*class="[^"]*bbs_file)[^>]*?>',
        # SNU ENG: strict .board_view → board_bot or file div
        r'<div[^>]*class="[^"]*board_view[^"]*"[^>]*>([\s\S]*?)<div[^>]*class="[^"]*(?:board_bot|bbs_file)[^"]*"[^>]*>',
        # CSE SNU (Drupal) pattern: .view-mode-full → .node__links
        r'<div[^>]*class="[^"]*view-mode-full[^"]*"[^>]*>([\s\S]*?)<div[^>]*class="[^"]*node__links[^"]*"[^>]*>',
        # Fallback: CSE .field-items → .comment-wrapper
        r'<div[^>]*class="[^"]*field-items[^"]*"[^>]*>([\s\S]*?)<div[^>]*class="[^"]*comment-wrapper[^"]*"[^>]*>',
        # Generic Drupal: .field-item
        r'<div[^>]*class="[^"]*field-item[^"]*"[^>]*>([\s\S]*?)</div>',
        # Generic to prevent external nav
        r'(<article[^>]*>[\s\S]*?</article>)',
        r'(<main[^>]*>[\s\S]*?</main>)',
    ]:
        m = re.search(pat, html, flags=re.I)
        if m and len(m.group(1)) > 100:
            return m.group(1)
    return html  # fallback to full HTML


def clean_html(text: str) -> str:
    # First try to extract content area only
    text = extract_content_area(text)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<nav[\s\S]*?</nav>", " ", text, flags=re.I)
    text = re.sub(r"<header[\s\S]*?</header>", " ", text, flags=re.I)
    text = re.sub(r"<footer[\s\S]*?</footer>", " ", text, flags=re.I)
    text = re.sub(r'<div[^>]*class="[^"]*(?:menu|nav|sidebar|gnb|lnb|snb|footer|header)[^"]*"[^>]*>[\s\S]*?</div>', " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Strip SNU ENG site nav boilerplate that leaks through
    text = re.sub(
        r"^.*?(?:본문\s*바로가기|메뉴\s*바로가기).*?(?:모바일\s*메뉴\s*닫기|Login\s*ENG)\s*",
        "", text, flags=re.I,
    )
    # Strip ENG site full nav block: "재학생 공지사항 학사안내 ... 규정자료실 일반인 교육 홍보광장 ... 캠퍼스안내"
    text = re.sub(
        r"재학생\s+공지사항\s+학사안내\s+학생활동\s+공대장학금[\s\S]{0,800}?캠퍼스안내\s*(?:예비\s*공대인[\s\S]{0,200}?캠퍼스안내\s*)?(?:졸업생[\s\S]{0,200}?캠퍼스안내\s*)?",
        " ", text, flags=re.I,
    )
    # Strip CSE site breadcrumb: "소식 공지사항 공지사항"
    text = re.sub(r"^소식\s+공지사항\s+공지사항\s*", "", text)
    # Strip common post metadata prefix: "작성자 : ... 작성 날짜 : YYYY/M/D ..."
    text = re.sub(r"작성자\s*:\s*\S+\s*작성\s*날짜\s*:\s*\S+\s*(?:\([^)]*\)\s*)?(?:오[전후]\s*\d+:\d+\s*)?", "", text)
    # Strip leftover nav keywords
    text = re.sub(
        r"(?:열린공대|창의설계축전|자랑스런공대인|규정자료실|홍보광장|서울공대\s*웹진|공대상상\s*웹진|예비\s*공대인)",
        " ", text, flags=re.I,
    )
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str, retries: int = 3, timeout: int = 25) -> str:
    err = None
    for _ in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:
            err = e
    raise err


def fetch_bytes(url: str, max_size: int, timeout: int = ATTACHMENT_DOWNLOAD_TIMEOUT) -> Optional[bytes]:
    """Download binary content with size cap. Returns None on failure."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
        r.raise_for_status()
        chunks = []
        total = 0
        for chunk in r.iter_content(8192):
            total += len(chunk)
            if total > max_size:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Source parsers (unchanged from v3)
# ---------------------------------------------------------------------------
def parse_eng_list(html: str) -> List[dict]:
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        if "boardId=" not in tr:
            continue
        m_link = re.search(r'href="([^"]*boardId=\d+[^"]*)"', tr)
        m_title = re.search(r"<a[^>]*>(.*?)</a>", tr, flags=re.S | re.I)
        m_date = re.search(r'<td class="date">\s*([^<]+)\s*</td>', tr)
        if not (m_link and m_title):
            continue
        href = re.sub(r";jsessionid=[^?]+", "", m_link.group(1))
        out.append({
            'source': 'ENG',
            'source_name': '서울대 공과대학 공지',
            'title': clean_html(m_title.group(1)),
            'url': urljoin('https://eng.snu.ac.kr/snu/bbs/BMSR00004/', href),
            'posted': m_date.group(1).strip() if m_date else '미확인',
        })
    return out


def parse_cse_list(html: str, limit: int = 80) -> List[dict]:
    out, seen = [], set()
    pat = re.compile(
        r'href="(/(?:ko/)?community/notice/(\d+))"[^>]*>\s*<span[^>]*>(.*?)</span>',
        re.S | re.I,
    )
    for m in pat.finditer(html):
        nid = m.group(2)
        if nid in seen:
            continue
        seen.add(nid)
        out.append({
            'source': 'CSE',
            'source_name': '서울대 컴퓨터공학부 공지',
            'title': clean_html(m.group(3)),
            'url': urljoin('https://cse.snu.ac.kr', m.group(1)),
            'posted': '미확인',
        })
        if len(out) >= limit:
            break
    return out


def extract_attachments(html: str, base_url: str) -> List[str]:
    urls = []
    for href in re.findall(r'href="([^"]+)"', html, flags=re.I):
        h = href.lower()
        if any(ext in h for ext in ['.pdf', '.hwp', '.hwpx', '.doc', '.docx', '.xls', '.xlsx', 'download']):
            urls.append(urljoin(base_url, href))
    dedup, seen = [], set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup[:10]


# ---------------------------------------------------------------------------
# v4: Attachment text extraction
# ---------------------------------------------------------------------------
def _ext_lower(url: str) -> str:
    """Best-effort extension from URL."""
    path = urlparse(url).path.lower()
    for ext in ['.hwpx', '.hwp', '.docx', '.doc', '.pdf']:
        if ext in path:
            return ext
    return Path(path).suffix.lower()


def parse_pdf_bytes(data: bytes) -> str:
    if pdfplumber is None:
        return ""
    try:
        pages_text = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:30]:  # cap pages
                t = page.extract_text()
                if t:
                    pages_text.append(t)
        return "\n".join(pages_text)
    except Exception:
        return ""


def parse_hwp_bytes(data: bytes) -> str:
    """Extract full text from .hwp via olefile BodyText sections (not PrvText preview)."""
    if olefile is None:
        return ""
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
        dirs = ole.listdir()

        # Validate HWP structure
        if ["FileHeader"] not in dirs:
            # Fallback to PrvText if not standard HWP
            if ole.exists("PrvText"):
                raw = ole.openstream("PrvText").read()
                ole.close()
                return raw.decode("utf-16-le", errors="replace").replace("\x00", "").strip()
            ole.close()
            return ""

        # Check if compressed
        header_data = ole.openstream("FileHeader").read()
        is_compressed = (header_data[36] & 1) == 1 if len(header_data) > 36 else False

        # Find all BodyText sections
        section_nums = []
        for d in dirs:
            if d[0] == "BodyText":
                try:
                    section_nums.append(int(d[1][len("Section"):]))
                except (ValueError, IndexError):
                    pass

        if not section_nums:
            # No BodyText, fallback to PrvText
            if ole.exists("PrvText"):
                raw = ole.openstream("PrvText").read()
                ole.close()
                return raw.decode("utf-16-le", errors="replace").replace("\x00", "").strip()
            ole.close()
            return ""

        # Extract text from each section
        full_text = ""
        for num in sorted(section_nums):
            stream_name = f"BodyText/Section{num}"
            raw_data = ole.openstream(stream_name).read()

            if is_compressed:
                try:
                    unpacked = zlib.decompress(raw_data, -15)
                except Exception:
                    unpacked = raw_data
            else:
                unpacked = raw_data

            # Parse records: rec_type 67 = paragraph text
            i = 0
            size = len(unpacked)
            while i + 4 <= size:
                header_val = struct.unpack_from("<I", unpacked, i)[0]
                rec_type = header_val & 0x3FF
                rec_len = (header_val >> 20) & 0xFFF

                if rec_type == 67 and i + 4 + rec_len <= size:
                    rec_data = unpacked[i + 4 : i + 4 + rec_len]
                    try:
                        full_text += rec_data.decode("utf-16-le", errors="replace")
                        full_text += "\n"
                    except Exception:
                        pass

                i += 4 + rec_len
                if rec_len == 0:
                    i += 4  # prevent infinite loop on zero-length records

            full_text += "\n"

        ole.close()
        # Clean up control characters
        full_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', full_text)
        return full_text.strip()
    except Exception:
        pass
    return ""


def parse_hwpx_bytes(data: bytes) -> str:
    """Extract text from .hwpx (ZIP with XML sections)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        texts = []
        for name in sorted(zf.namelist()):
            if 'section' in name.lower() and name.endswith('.xml'):
                xml_data = zf.read(name).decode("utf-8", errors="replace")
                # strip XML tags to get plain text
                plain = re.sub(r"<[^>]+>", " ", xml_data)
                plain = re.sub(r"\s+", " ", plain).strip()
                if plain:
                    texts.append(plain)
        zf.close()
        return "\n".join(texts)
    except Exception:
        return ""


def parse_docx_bytes(data: bytes) -> str:
    if python_docx is None:
        return ""
    try:
        doc = python_docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


def extract_attachment_text(url: str) -> str:
    """Download and parse a single attachment. Returns extracted text or empty string."""
    ext = _ext_lower(url)
    if ext == '.pdf':
        data = fetch_bytes(url, PDF_MAX_SIZE)
        if data:
            return parse_pdf_bytes(data)
    elif ext == '.hwp':
        data = fetch_bytes(url, PDF_MAX_SIZE)
        if data:
            return parse_hwp_bytes(data)
    elif ext == '.hwpx':
        data = fetch_bytes(url, PDF_MAX_SIZE)
        if data:
            return parse_hwpx_bytes(data)
    elif ext == '.docx':
        data = fetch_bytes(url, PDF_MAX_SIZE)
        if data:
            return parse_docx_bytes(data)
    # .doc skipped (binary format)
    return ""


def process_attachments(attachment_urls: List[str]) -> str:
    """Process all attachments for a notice, respecting limits and timeouts."""
    texts = []
    pdf_count = 0
    start = datetime.now()

    for url in attachment_urls:
        elapsed = (datetime.now() - start).total_seconds()
        if elapsed > ATTACHMENT_TOTAL_TIMEOUT:
            texts.append("[첨부 처리 시간 초과]")
            break

        ext = _ext_lower(url)
        if ext == '.pdf':
            if pdf_count >= PDF_MAX_PER_NOTICE:
                continue
            pdf_count += 1

        fname = Path(urlparse(url).path).name or url[-40:]
        try:
            t = extract_attachment_text(url)
            if t:
                texts.append(f"[첨부:{fname}] {t[:3000]}")
        except Exception:
            texts.append(f"[첨부 파싱 실패: {fname}]")

    return "\n".join(texts)


# ---------------------------------------------------------------------------
# v4: Image OCR
# ---------------------------------------------------------------------------
def extract_image_urls(html: str, base_url: str) -> List[str]:
    """Find image URLs within the content area of an HTML page."""
    urls = []
    for src in re.findall(r'<img[^>]+src="([^"]+)"', html, flags=re.I):
        sl = src.lower()
        if any(sl.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
            urls.append(urljoin(base_url, src))
    # dedupe
    seen = set()
    deduped = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped[:IMG_MAX_PER_NOTICE]


def ocr_image_bytes(data: bytes) -> str:
    """Run Tesseract OCR on image bytes with Korean+English."""
    if not HAS_TESSERACT or Image is None:
        return ""
    try:
        import subprocess
        img = Image.open(io.BytesIO(data)).convert("L")  # grayscale
        # simple threshold for better OCR
        img = img.point(lambda x: 0 if x < 140 else 255, '1')
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            tmp_path = tmp.name
        result = subprocess.run(
            ["tesseract", tmp_path, "stdout", "-l", "kor+eng", "--psm", "6"],
            capture_output=True, text=True, timeout=30,
        )
        Path(tmp_path).unlink(missing_ok=True)
        return result.stdout.strip()
    except Exception:
        return ""


def process_images(html: str, base_url: str) -> str:
    """Extract and OCR images found in the notice HTML."""
    img_urls = extract_image_urls(html, base_url)
    if not img_urls:
        return ""
    texts = []
    for url in img_urls:
        try:
            data = fetch_bytes(url, IMG_MAX_SIZE)
            if data:
                t = ocr_image_bytes(data)
                if t and len(t) > 10:
                    texts.append(f"[이미지OCR] {t[:2000]}")
        except Exception:
            pass
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# v4: External link following
# ---------------------------------------------------------------------------
def find_external_links(html: str, base_url: str) -> List[str]:
    """Find links to known external scholarship sites."""
    found = []
    for href in re.findall(r'href="([^"]+)"', html, flags=re.I):
        full = urljoin(base_url, href)
        domain = urlparse(full).netloc.lower()
        if any(d in domain for d in EXTERNAL_LINK_DOMAINS):
            if full not in found:
                found.append(full)
    # Also detect Korean scholarship foundation patterns
    for href in re.findall(r'href="([^"]+)"', html, flags=re.I):
        full = urljoin(base_url, href)
        if '장학' in full or 'scholarship' in full.lower():
            domain = urlparse(full).netloc.lower()
            base_domain = urlparse(base_url).netloc.lower()
            if domain != base_domain and full not in found:
                found.append(full)
    return found[:EXTERNAL_LINK_MAX]


def follow_external_links(html: str, base_url: str) -> tuple:
    """Follow external links and return (extracted_text, link_list)."""
    links = find_external_links(html, base_url)
    texts = []
    for link in links:
        try:
            page = fetch(link, retries=2, timeout=20)
            t = clean_html(page)[:3000]
            if t:
                texts.append(f"[외부링크:{urlparse(link).netloc}] {t}")
        except Exception:
            pass
    return "\n".join(texts), links


# ---------------------------------------------------------------------------
# v4: Enhanced extraction
# ---------------------------------------------------------------------------
def _normalize_date(raw: str) -> str:
    """Normalize various date formats to YYYY.M.D and validate."""
    # Remove spaces around dots/slashes: "2026. 4. 13." → "2026.4.13"
    d = re.sub(r"\s*[.]\s*", ".", raw.strip().rstrip("."))
    d = re.sub(r"\s*[-]\s*", "-", d)
    d = re.sub(r"\s*[/]\s*", "/", d)
    return d


def _is_future_date(date_str: str) -> bool:
    """Check if a date string is in the future (or within 7 days past). Rejects old dates."""
    try:
        # Normalize separators to dots
        d = date_str.replace("-", ".").replace("/", ".")
        parts = d.split(".")
        if len(parts) >= 3:
            y, m, day = int(parts[0]), int(parts[1]), int(parts[2])
            from datetime import timedelta
            target = datetime(y, m, day)
            # Accept dates up to 7 days in the past (recent deadlines) and any future
            return target >= datetime.now() - timedelta(days=7)
    except Exception:
        pass
    return True  # if can't parse, don't filter out


def extract_deadline(text: str) -> str:
    patterns = [
        # "2026.3.18(월)까지", "2026-03-18 까지", "2026/3/18 마감"
        # Also: "2026. 3. 18.(월) 까지" (with spaces)
        r"(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})\.?\s*(?:\([월화수목금토일]\))?\s*(?:까지|마감)",
        # 신청기간: ~DATE or 신청기간: DATE
        r"(?:신청기간|접수기간|제출기한|마감일)\s*[:：]?\s*(?:~\s*)?(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})",
        r"마감[^\n]{0,40}(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})",
        # "A ~ 2026.3.31" range pattern (take end date)
        r"(?:20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})\s*~\s*(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})",
        # "~2026.3.18(화)", "~ 2026/3/18", "~ 2026. 3. 18."
        r"~\s*(20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})\.?\s*(?:\([월화수목금토일]\))?",
        # "~3/18", "~ 3.18", "~3.18(화)" (current year implied)
        r"~\s*(\d{1,2})\s*[./]\s*(\d{1,2})\s*(?:\([월화수목금토일]\))?",
        # "3.18(월)까지", "3월 18일 까지"
        r"(\d{1,2})\s*[.월]\s*(\d{1,2})\s*일?\s*(?:\([월화수목금토일]\))?\s*까지",
        # "2026년 3월 18일"
        r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        # Last resort: "2026. 4. 17.( 금 ) 17:00" (spaced date near 접수/신청/제출)
        r"(?:접수|신청|제출)[^\n]{0,60}(20\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2})\s*\.?\s*\(",
    ]
    for i, p in enumerate(patterns):
        m = re.search(p, text)
        if m:
            if i <= 4 or i == 8:
                raw = _normalize_date(m.group(1))
                if _is_future_date(raw):
                    return raw
            elif i == 5:
                year = datetime.now().year
                result = f"{year}.{m.group(1).strip()}.{m.group(2).strip()}"
                if _is_future_date(result):
                    return result
            elif i == 6:
                year = datetime.now().year
                result = f"{year}.{m.group(1).strip()}.{m.group(2).strip()}"
                if _is_future_date(result):
                    return result
            elif i == 7:
                result = f"{m.group(1).strip()}.{m.group(2).strip()}.{m.group(3).strip()}"
                if _is_future_date(result):
                    return result
    return "원문 확인 필요"


def extract_income(text: str) -> str:
    patterns = [
        r"([^\.\n]{0,50}소득분위[^\.\n]{0,100})",
        r"([^\.\n]{0,50}소득구간[^\.\n]{0,100})",
        # "기초/차상위", "기초생활수급자"
        r"((?:기초|차상위)[^\.\n]{0,80})",
        # "1~3분위", "8분위 이하"
        r"(\d+\s*[~-]\s*\d+\s*분위[^\.\n]{0,60})",
        r"(\d+\s*분위\s*이하[^\.\n]{0,60})",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:120]
    return "명시 없음 (원문/첨부 확인 필요)"


def extract_amount(text: str) -> str:
    """Extract scholarship amount (장학금액). Clean, concise output."""
    # Try labeled amount first: "장학금액: ...", "지원금액: ..."
    m = re.search(r"(?:장학금액|지원금액|지원내용)\s*[:：]\s*([^\n]{5,80})", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(1)).strip()
        # Truncate at first unrelated keyword
        val = re.split(r"(?:신청|접수|제출|선발|구비|서류|문의)", val)[0].strip().rstrip(".,;: ")
        return val[:100]

    patterns = [
        # "등록금 전액 및 취·창업지원금 200만원" — combined
        r"(등록금\s*(?:전액|반액|일부)(?:\s*(?:및|,)\s*[\w·]+\s*\d[\d,]*\s*만\s*원)?)",
        # "등록금 전액", "등록금 반액"
        r"(등록금\s*(?:전액|반액|일부))",
        # "480만원", "500 만원", "4,000만원" — capture just the amount phrase
        r"(\d[\d,]*\s*만\s*원)",
        # "5,000,000원", "500000원" — just the number+원
        r"([\d,]{4,}\s*원)",
        # "전액 지원", "반액 지원", "전액장학", "반액장학금"
        r"((?:전액|반액)\s*(?:지원|감면|면제|장학금?))",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:100]
    return ""


def extract_target(text: str) -> str:
    """Extract target audience (지원대상)."""
    # try explicit "지원대상" section first
    m = re.search(r"지원\s*대상\s*[:：]\s*([^\n]{5,120})", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(1)).strip()
        # Truncate at next section keyword
        val = re.split(r"(?:장학금액|지원금액|신청기간|접수기간|신청방법|제출서류|선발방법|문의)", val)[0].strip().rstrip(".,;:○ ")
        return val[:120]
    # keywords
    keywords = []
    if re.search(r"학부", text):
        keywords.append("학부")
    if re.search(r"대학원", text):
        keywords.append("대학원")
    if re.search(r"석사", text):
        keywords.append("석사")
    if re.search(r"박사", text):
        keywords.append("박사")
    if re.search(r"신입생", text):
        keywords.append("신입생")
    if re.search(r"재학생", text):
        keywords.append("재학생")
    if keywords:
        return ", ".join(keywords)
    return ""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
def make_summary(text: str, amount: str = "", target: str = "") -> str:
    """Generate a concise summary from cleaned body text."""
    t = text[:3000]
    # Strip any residual boilerplate
    t = re.sub(r"(다음글|이전글|목록|사이트맵|개인정보처리방침)[\s\S]*$", " ", t)
    t = re.sub(r"(본문\s*바로가기|메뉴\s*바로가기|모바일\s*메뉴\s*닫기|Login\s*ENG)", " ", t)
    t = re.sub(r"서울대학교\s*(?:공과대학|컴퓨터공학부)", " ", t)
    t = re.sub(r"Department of Computer Science", " ", t, flags=re.I)
    t = re.sub(r"소식\s+공지사항\s+공지사항", " ", t)
    t = re.sub(r"작성자\s*:\s*\S+\s*작성\s*날짜\s*:\s*[^\n]{0,40}", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    # Find first meaningful content keyword
    keywords = ['장학', '지원', '신청', '모집', '선발', '추천', '대상', '자격', '기간', '금액', '제출', '마감', '안내']
    for k in keywords:
        idx = t.find(k)
        if 0 < idx < 500:
            t = t[max(0, idx - 15):]
            break

    # Try to extract a meaningful sentence
    m = re.search(r"((?:신청기간|지원대상|신청방법|지원내용|선발방법|지원금액|장학금명?|모집|추천|안내)[^\n]{0,140})", t)
    base = m.group(1).strip()[:120] if m else t[:120].strip()

    extras = []
    if amount:
        extras.append(f"금액:{amount[:40]}")
    if target:
        extras.append(f"대상:{target[:40]}")
    if extras:
        base = f"{base} | {' | '.join(extras)}"
    return base[:200]


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\[[^\]]+\]", " ", t)
    t = re.sub(r"\([^\)]*\)", " ", t)
    t = re.sub(r"[^가-힣a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_scholarship(title: str, body: str) -> bool:
    tl = title.lower()
    if any(x in tl for x in EXCLUDE_IN_TITLE):
        return False
    # Only check title + first 1200 chars of CLEANED body (nav already stripped)
    # Additional title-only fast-pass for obvious scholarship notices
    if any(k in tl for k in ['장학', '학자금', '등록금', '대출이자']):
        return True
    # Check body but exclude common nav false-positives
    body_clean = body[:1200]
    # Remove any remaining nav-like phrases that could contain "장학"
    body_clean = re.sub(r"공대장학금", " ", body_clean)
    body_clean = re.sub(r"장학금\s*(?:열린공대|공지사항|학사안내)", " ", body_clean)
    joined = f"{title} {body_clean}".lower()
    return any(k in joined for k in [k.lower() for k in INCLUDE_KEYWORDS])


def score_notice(n: Notice) -> int:
    score = 0
    if n.deadline != "원문 확인 필요":
        score += 2
    if n.income_condition != "명시 없음 (원문/첨부 확인 필요)":
        score += 2
    score += min(len(n.attachments), 3)
    score += 1 if len(n.summary) > 20 else 0
    if n.amount:
        score += 1
    if n.target:
        score += 1
    if n.external_links:
        score += 1
    return score


def build_dedupe_key(title: str, attachments: List[str]) -> str:
    key = normalize_title(title)
    att = "|".join(sorted([Path(a).name.lower() for a in attachments]))
    return hashlib.sha1((key + '|' + att).encode()).hexdigest()[:16]


def enrich_notice(seed: dict, html: str) -> Optional[Notice]:
    """Build a Notice from a seed + fetched HTML, with v4 enrichment."""
    txt = clean_html(html)
    atts = extract_attachments(html, seed['url'])

    if not is_scholarship(seed['title'], txt):
        return None

    # v4: enrich with attachment text
    att_text = ""
    try:
        att_text = process_attachments(atts)
    except Exception:
        att_text = "[첨부 일괄 파싱 실패]"

    # v4: enrich with image OCR
    ocr_text = ""
    try:
        ocr_text = process_images(html, seed['url'])
    except Exception:
        pass

    # v4: follow external links
    ext_text = ""
    ext_links = []
    try:
        ext_text, ext_links = follow_external_links(html, seed['url'])
    except Exception:
        pass

    # Merge all text sources
    parts = [txt[:4500]]
    if att_text:
        parts.append(att_text[:4000])
    if ocr_text:
        parts.append(ocr_text[:2000])
    if ext_text:
        parts.append(ext_text[:3000])
    body_focus = "\n".join(parts)

    dedupe_key = build_dedupe_key(seed['title'], atts)

    # --- LLM extraction (primary) with regex fallback ---
    llm = _llm_extract_fields(body_focus, title=seed['title'])
    relevant = True
    relevance_reason = ""
    if llm:
        deadline = llm.get("deadline") or extract_deadline(body_focus)
        income = llm.get("income_condition") or extract_income(body_focus)
        amount = llm.get("amount") or extract_amount(body_focus)
        target = llm.get("target") or extract_target(body_focus)
        summary = llm.get("summary") or make_summary(body_focus, amount, target)
        relevant = bool(llm.get("relevant", True))
        relevance_reason = llm.get("relevance_reason", "")
        time.sleep(_LLM_CALL_DELAY)
    else:
        deadline = extract_deadline(body_focus)
        income = extract_income(body_focus)
        amount = extract_amount(body_focus)
        target = extract_target(body_focus)
        summary = make_summary(body_focus, amount, target)

    return Notice(
        source=seed['source_name'],
        title=seed['title'],
        url=seed['url'],
        posted=seed['posted'],
        detail_text=body_focus,
        attachments=atts,
        deadline=deadline,
        income_condition=income,
        summary=summary,
        content_hash=hashlib.sha256(
            (seed['title'] + body_focus[:1200]).encode()
        ).hexdigest()[:16],
        dedupe_key=dedupe_key,
        merged_sources=[seed['source']],
        external_links=ext_links,
        amount=amount,
        target=target,
        relevant=relevant,
        relevance_reason=relevance_reason,
    )


def collect() -> List[Notice]:
    seeds = parse_eng_list(fetch(ENG_LIST)) + parse_cse_list(fetch(CSE_LIST))
    out: List[Notice] = []
    for s in seeds:
        try:
            html = fetch(s['url'])
            notice = enrich_notice(s, html)
            if notice:
                out.append(notice)
        except Exception as e:
            # fallback: minimal notice from title
            txt = s['title']
            if not is_scholarship(s['title'], txt):
                continue
            out.append(Notice(
                source=s['source_name'],
                title=s['title'],
                url=s['url'],
                posted=s['posted'],
                detail_text=txt,
                attachments=[],
                deadline=extract_deadline(txt),
                income_condition=extract_income(txt),
                summary=make_summary(txt),
                content_hash=hashlib.sha256(txt.encode()).hexdigest()[:16],
                dedupe_key=build_dedupe_key(s['title'], []),
                merged_sources=[s['source']],
            ))
    return out


def dedupe_notices(items: List[Notice]) -> List[Notice]:
    groups: Dict[str, Notice] = {}
    for n in items:
        if n.dedupe_key not in groups:
            groups[n.dedupe_key] = n
            continue
        cur = groups[n.dedupe_key]
        if score_notice(n) > score_notice(cur):
            n.merged_sources = sorted(list(set(cur.merged_sources + n.merged_sources)))
            groups[n.dedupe_key] = n
        else:
            cur.merged_sources = sorted(list(set(cur.merged_sources + n.merged_sources)))
            groups[n.dedupe_key] = cur
    return list(groups.values())


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def append_lines(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        for l in lines:
            f.write(l + '\n')


# ---------------------------------------------------------------------------
# Queue writer (updated for v4 fields)
# ---------------------------------------------------------------------------
def write_queue(today: str, new_items: List[Notice], updated_items: List[Notice]):
    qpath = QUEUE_DIR / f"{today}.md"
    mpath = STATE_DIR / f"queue_map_{today}.json"

    # Split by relevance
    new_relevant = [n for n in new_items if n.relevant]
    new_irrelevant = [n for n in new_items if not n.relevant]
    upd_relevant = [n for n in updated_items if n.relevant]
    upd_irrelevant = [n for n in updated_items if not n.relevant]

    queue_map = {"new": {}, "updated": {}, "skipped": {}}
    lines = [
        f"# Scholarship Approval Queue - {today}",
        "",
        "승인 정책: 한 줄 한 액션 체크 방식 (Obsidian-safe)",
        "",
        f"- 해당 신규: {len(new_relevant)}건",
        f"- 해당 변경: {len(upd_relevant)}건",
        f"- 비해당 (자동 스킵): {len(new_irrelevant) + len(upd_irrelevant)}건",
        "",
        "## 신규(New)",
    ]
    if not new_relevant:
        lines.append("- (없음)")

    def _notice_block(nid: str, n: Notice) -> List[str]:
        merged = "+".join(n.merged_sources)
        block = [
            f"### {nid} {n.title}",
            f"- 게시 기관: {n.source}",
            f"- 마감일: {n.deadline}",
            f"- 소득분위 조건: {n.income_condition}",
        ]
        if n.amount:
            block.append(f"- 장학금액: {n.amount}")
        if n.target:
            block.append(f"- 지원대상: {n.target}")
        block += [
            f"- 요약: {n.summary}",
            f"- 링크: {n.url}",
        ]
        if n.external_links:
            block.append(f"- 외부링크: {', '.join(n.external_links)}")
        block += [
            f"- 중복통합: {merged}",
            f"- [ ] {nid} | 보관",
            f"- [ ] {nid} | 알림",
            f"- [ ] {nid} | 캘린더",
            f"- [ ] {nid} | 무시",
            "",
        ]
        return block

    for i, n in enumerate(new_relevant, 1):
        nid = f"NEW-{i}"
        queue_map["new"][nid] = asdict(n)
        lines += _notice_block(nid, n)

    lines += ["## 변경(Updated)"]
    if not upd_relevant:
        lines.append("- (없음)")
    for i, n in enumerate(upd_relevant, 1):
        uid = f"UPD-{i}"
        queue_map["updated"][uid] = asdict(n)
        lines += _notice_block(uid, n)

    # Collapsed section for irrelevant notices
    all_irrelevant = new_irrelevant + upd_irrelevant
    if all_irrelevant:
        lines += [
            "",
            "---",
            f"## 비해당 ({len(all_irrelevant)}건, 자동 스킵)",
            "> 프로필 기준 지원 불가로 판단된 공지. 오판 시 수동으로 보관/알림 체크 가능.",
            "",
        ]
        for i, n in enumerate(all_irrelevant, 1):
            sid = f"SKIP-{i}"
            queue_map["skipped"][sid] = asdict(n)
            reason = f" — ❌ {n.relevance_reason}" if n.relevance_reason else ""
            lines += [
                f"### {sid} {n.title}{reason}",
                f"- 마감일: {n.deadline} | 대상: {n.target}",
                f"- 링크: {n.url}",
                f"- [ ] {sid} | 보관 (오판 시)",
                "",
            ]

    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text("\n".join(lines).strip() + "\n", encoding='utf-8')
    save_json(mpath, queue_map)
    return qpath, mpath


# ---------------------------------------------------------------------------
# CLI: run
# ---------------------------------------------------------------------------
def cmd_run():
    for d in [RAW_DIR, QUEUE_DIR, APPROVED_DIR, STATE_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # capability report
    caps = []
    if pdfplumber:
        caps.append("PDF")
    if olefile:
        caps.append("HWP")
    caps.append("HWPX")  # stdlib zipfile
    if python_docx:
        caps.append("DOCX")
    if HAS_TESSERACT:
        caps.append("OCR")
    caps.append("ExtLinks")
    print(f"[v4] capabilities: {', '.join(caps)}")

    today = datetime.now().strftime('%Y-%m-%d')
    items = dedupe_notices(collect())

    raw_path = RAW_DIR / f"{today}.json"
    save_json(raw_path, [asdict(x) for x in items])

    latest = load_json(
        STATE_DIR / 'latest_index.json', {'url_to_hash': {}}
    ).get('url_to_hash', {})
    approved_idx = load_json(
        STATE_DIR / 'approved_index.json', {'url_to_hash': {}}
    ).get('url_to_hash', {})

    new_items, updated_items = [], []
    for n in items:
        if approved_idx.get(n.url) == n.content_hash:
            continue
        old = latest.get(n.url)
        if old is None:
            new_items.append(n)
        elif old != n.content_hash:
            updated_items.append(n)

    save_json(STATE_DIR / 'latest_index.json', {
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'url_to_hash': {n.url: n.content_hash for n in items}
    })

    queue_path, map_path = write_queue(today, new_items, updated_items)
    print(json.dumps({
        'raw_path': str(raw_path),
        'queue_path': str(queue_path),
        'queue_map_path': str(map_path),
        'total_collected': len(items),
        'new_count': len(new_items),
        'updated_count': len(updated_items),
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI: apply
# ---------------------------------------------------------------------------
def cmd_apply():
    today = datetime.now().strftime('%Y-%m-%d')
    qpath = QUEUE_DIR / f'{today}.md'
    mpath = STATE_DIR / f'queue_map_{today}.json'
    if not qpath.exists() or not mpath.exists():
        print('NO_QUEUE')
        return

    queue_map = load_json(mpath, {'new': {}, 'updated': {}})
    txt = qpath.read_text(encoding='utf-8')

    checked = re.findall(
        r'^- \[(x|X)\] (NEW-\d+|UPD-\d+) \| (보관|알림|캘린더|무시)\s*$',
        txt, flags=re.M,
    )
    if not checked:
        print('NO_CHECKED_ACTIONS')
        return

    by_id = {}
    for _, item_id, action in checked:
        by_id.setdefault(item_id, set()).add(action)

    approved_index = load_json(STATE_DIR / 'approved_index.json', {'url_to_hash': {}})
    ignore_index = load_json(STATE_DIR / 'ignore_index.json', {'url_to_hash': {}})

    approved_lines = [f'# Approved Scholarship - {today}', '']
    processed_ids = []

    for item_id, actions in by_id.items():
        obj = queue_map['new'].get(item_id) or queue_map['updated'].get(item_id)
        if not obj:
            continue

        if '무시' in actions:
            ignore_index['url_to_hash'][obj['url']] = obj['content_hash']
            processed_ids.append(item_id)
            continue

        if '보관' in actions:
            approved_index['url_to_hash'][obj['url']] = obj['content_hash']
            block = [
                f"## {item_id} {obj['title']}",
                f"- 게시 기관: {obj['source']}",
                f"- 마감일: {obj['deadline']}",
                f"- 소득분위 조건: {obj['income_condition']}",
            ]
            if obj.get('amount'):
                block.append(f"- 장학금액: {obj['amount']}")
            if obj.get('target'):
                block.append(f"- 지원대상: {obj['target']}")
            block += [
                f"- 요약: {obj['summary']}",
                f"- 링크: {obj['url']}",
                f"- 승인 액션: {', '.join(sorted(actions))}",
                '',
            ]
            approved_lines += block

        if '알림' in actions:
            append_lines(ALERT_QUEUE, [
                f"- [ ] {item_id} {obj['title']} | {obj['url']}"
            ])

        if '캘린더' in actions:
            append_lines(CAL_QUEUE, [
                f"- [ ] {item_id} {obj['title']} | 마감:{obj['deadline']} | {obj['url']}"
            ])

        processed_ids.append(item_id)

    if len(approved_lines) > 2:
        apath = APPROVED_DIR / f'{today}.md'
        apath.write_text('\n'.join(approved_lines).strip() + '\n', encoding='utf-8')

    save_json(STATE_DIR / 'approved_index.json', approved_index)
    save_json(STATE_DIR / 'ignore_index.json', ignore_index)

    new_txt = txt
    for pid in processed_ids:
        new_txt = re.sub(
            rf'^(### {re.escape(pid)} .*)$',
            r'\1 ✅ processed', new_txt, flags=re.M,
        )
    qpath.write_text(new_txt, encoding='utf-8')

    print(json.dumps({
        'processed_count': len(processed_ids),
        'processed_ids': sorted(set(processed_ids)),
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI: status
# ---------------------------------------------------------------------------
def cmd_status():
    today = datetime.now().strftime('%Y-%m-%d')
    latest = load_json(STATE_DIR / 'latest_index.json', {})
    approved = load_json(STATE_DIR / 'approved_index.json', {'url_to_hash': {}})
    ignored = load_json(STATE_DIR / 'ignore_index.json', {'url_to_hash': {}})
    raw_today = RAW_DIR / f"{today}.json"
    queue_today = QUEUE_DIR / f"{today}.md"

    caps = []
    if pdfplumber:
        caps.append("PDF")
    if olefile:
        caps.append("HWP")
    caps.append("HWPX")
    if python_docx:
        caps.append("DOCX")
    if HAS_TESSERACT:
        caps.append("OCR")
    caps.append("ExtLinks")

    print(json.dumps({
        'version': 'v4',
        'capabilities': caps,
        'today': today,
        'last_updated': latest.get('updated_at', 'never'),
        'total_tracked': len(latest.get('url_to_hash', {})),
        'approved_count': len(approved.get('url_to_hash', {})),
        'ignored_count': len(ignored.get('url_to_hash', {})),
        'raw_exists_today': raw_today.exists(),
        'queue_exists_today': queue_today.exists(),
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Scholarship Pipeline v4")
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('run', help='Crawl, enrich, and generate approval queue')
    sub.add_parser('apply', help='Apply checked actions from today\'s queue')
    sub.add_parser('status', help='Show pipeline status and capabilities')

    args = parser.parse_args()

    if args.command == 'run':
        cmd_run()
    elif args.command == 'apply':
        cmd_apply()
    elif args.command == 'status':
        cmd_status()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

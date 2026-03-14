"""Core renderer: consumes widget_data.json, produces PNG images via Jinja2 + Playwright.

Supports three output profiles (square, standard, tall) driven by container queries.
Uses MD5 hash caching to skip redundant renders.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, BaseLoader

from .config import PROFILES, load_config

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"


# ---------------------------------------------------------------------------
# Hash / cache helpers
# ---------------------------------------------------------------------------

def _file_md5(path: Path) -> str:
    """Return hex MD5 digest of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def should_render(data_path: Path, hash_file: Path) -> bool:
    """Return True if the source data has changed since the last render.

    Compares MD5 of *data_path* against the hash stored in *hash_file*.
    Returns True (should render) if hash_file is missing or hashes differ.
    """
    if not data_path.exists():
        return False
    current = _file_md5(data_path)
    if not hash_file.exists():
        return True
    stored = hash_file.read_text(encoding="utf-8").strip()
    return current != stored


def _save_hash(data_path: Path, hash_file: Path) -> None:
    """Persist the current MD5 of data_path to hash_file."""
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(_file_md5(data_path), encoding="utf-8")


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def _load_css() -> str:
    """Read widget.css from the templates directory."""
    css_path = _TEMPLATE_DIR / "widget.css"
    return css_path.read_text(encoding="utf-8")


def _load_template(profile_name: str = "base") -> str:
    """Read a Jinja2 template source. Falls back to base.html."""
    custom = _TEMPLATE_DIR / f"{profile_name}.html"
    if custom.exists():
        return custom.read_text(encoding="utf-8")
    tpl_path = _TEMPLATE_DIR / "base.html"
    return tpl_path.read_text(encoding="utf-8")


def _render_html(template_src: str, css: str, data: dict[str, Any],
                 width: int, height: int) -> str:
    """Render the Jinja2 template with widget data and dimensions."""
    env = Environment(loader=BaseLoader(), autoescape=False)
    tpl = env.from_string(template_src)
    context = {
        "css": css,
        "width": width,
        "height": height,
        "meta": data.get("meta", {}),
        "date": data.get("date", {}),
        "schedule": data.get("schedule", []),
        "tasks": data.get("tasks", []),
        "goals": data.get("goals", []),
    }
    return tpl.render(**context)


# ---------------------------------------------------------------------------
# Screenshot via Playwright
# ---------------------------------------------------------------------------

def _screenshot(html: str, out_path: Path, *,
                width: int, height: int, dpr: int = 2) -> Path:
    """Write HTML to a temp file and capture a PNG screenshot with Playwright."""
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8",
    ) as f:
        f.write(html)
        tmp = Path(f.name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=dpr,
            )
            page.goto(f"file://{tmp}", wait_until="networkidle")
            # Allow web fonts to load
            page.wait_for_timeout(600)
            page.screenshot(path=str(out_path), clip={
                "x": 0, "y": 0, "width": width, "height": height,
            })
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)

    return out_path


# ---------------------------------------------------------------------------
# Meta / state persistence
# ---------------------------------------------------------------------------

def write_meta(output_dir: Path, data: dict[str, Any],
               profiles_rendered: list[str]) -> Path:
    """Write widget_meta.json alongside the rendered images."""
    source_hash = hashlib.md5(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()

    meta = {
        "last_success": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "source_date": data.get("date", {}).get("iso", "unknown"),
        "source_hash": source_hash,
        "profiles": profiles_rendered,
    }
    meta_path = output_dir / "widget_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta_path


def update_state(state_file: str | Path, success: bool,
                 error: str | None = None) -> None:
    """Track render attempts and consecutive failures in a state JSON file."""
    state_path = Path(state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

    now = datetime.now(timezone.utc).isoformat()
    state["last_attempt"] = now

    if success:
        state["last_success"] = now
        state["consecutive_failures"] = 0
        state["last_error"] = None
    else:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["last_error"] = error or "unknown"

    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def render_fallback(output_dir: Path) -> None:
    """Mark meta as stale without touching existing images."""
    meta_path = output_dir / "widget_meta.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    meta["status"] = "stale"
    meta["last_stale"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_all(
    data_path: str | Path,
    output_dir: str | Path,
    profiles: dict[str, dict[str, int]] | None = None,
    force: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Render widget PNGs for all (or specified) profiles.

    Parameters
    ----------
    data_path : path to widget_data.json
    output_dir : directory for output PNGs
    profiles : profile dict override (default: from config or PROFILES fallback)
    force : skip hash cache check
    cfg : pre-loaded config dict (optional)

    Returns
    -------
    dict mapping profile name -> output Path
    """
    if cfg is None:
        cfg = load_config()

    data_path = Path(data_path)
    output_dir = Path(output_dir)

    if not data_path.exists():
        raise FileNotFoundError(f"Widget data not found: {data_path}")

    # Cache check
    cache_cfg = cfg.get("cache", {})
    hash_file = Path(cache_cfg.get("hash_file", "~/.cache/obsidian_widget/last_hash")).expanduser()

    if cache_cfg.get("enabled", True) and not force:
        if not should_render(data_path, hash_file):
            return {}

    # Load data
    raw = data_path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)

    # Validate version
    version = data.get("meta", {}).get("version", 1)
    if version > 1:
        raise ValueError(
            f"Unsupported widget_data version {version} (max supported: 1)"
        )

    # Prepare CSS (shared across non-wallpaper profiles)
    css = _load_css()

    # Resolve profiles
    if profiles is None:
        profiles = cfg.get("profiles", PROFILES)

    output_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}
    state_file = cfg.get("logging", {}).get(
        "state_file", "automation/.state/obsidian_widget_state.json"
    )

    try:
        for name, prof in profiles.items():
            w = prof["width"]
            h = prof["height"]
            dpr = prof.get("dpr", 2)

            # Use profile-specific template if available (e.g. wallpaper.html)
            template_src = _load_template(name)
            html = _render_html(template_src, css, data, w, h)
            out_path = output_dir / f"widget_{name}.png"
            _screenshot(html, out_path, width=w, height=h, dpr=dpr)
            results[name] = out_path

        # Backward compatibility: copy standard -> latest_timetable.png
        std = output_dir / "widget_standard.png"
        latest = output_dir / "latest_timetable.png"
        if std.exists():
            shutil.copy2(std, latest)
            results["latest_timetable"] = latest

        # Write metadata
        write_meta(output_dir, data, list(profiles.keys()))

        # Update hash cache
        if cache_cfg.get("enabled", True):
            _save_hash(data_path, hash_file)

        # Update state
        update_state(state_file, success=True)

    except Exception as exc:
        update_state(state_file, success=False, error=str(exc))
        raise

    return results

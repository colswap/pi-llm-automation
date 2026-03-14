"""Configuration loader for the Obsidian widget renderer.

Reads config.yaml from the package directory, resolves ~ paths,
and provides typed accessors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_PKG_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _PKG_DIR / "config.yaml"

# Fallback profiles if config.yaml is missing or incomplete
PROFILES: dict[str, dict[str, int]] = {
    "square": {"width": 400, "height": 400, "dpr": 2},
    "standard": {"width": 800, "height": 600, "dpr": 2},
    "tall": {"width": 540, "height": 960, "dpr": 2},
}


def _resolve_path(raw: str) -> Path:
    """Expand ~ and resolve a path string."""
    return Path(raw).expanduser().resolve()


def load_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load and return configuration dict with resolved paths.

    Parameters
    ----------
    config_path : optional override for config.yaml location.

    Returns
    -------
    dict with all config keys; tilde-paths expanded.
    """
    path = Path(config_path) if config_path else _CONFIG_PATH
    if not path.exists():
        # Return sensible defaults so the renderer can still work
        return {
            "widget_data_source": str(
                Path("~/Obsidian/YuriVault/99_Sync/widget-data/widget_data.json").expanduser()
            ),
            "output_dir": str(Path("~/Sync/widget-timetable").expanduser()),
            "profiles": PROFILES,
            "theme": "samsung-dark",
            "stale_threshold_minutes": 120,
            "logging": {
                "state_file": "automation/.state/obsidian_widget_state.json",
            },
            "cache": {
                "enabled": True,
                "hash_file": str(Path("~/.cache/obsidian_widget/last_hash").expanduser()),
            },
        }

    with open(path, "r", encoding="utf-8") as fh:
        cfg: dict[str, Any] = yaml.safe_load(fh)

    # Resolve tilde-bearing paths
    for key in ("widget_data_source", "output_dir"):
        if key in cfg and isinstance(cfg[key], str):
            cfg[key] = str(_resolve_path(cfg[key]))

    if "cache" in cfg and "hash_file" in cfg["cache"]:
        cfg["cache"]["hash_file"] = str(_resolve_path(cfg["cache"]["hash_file"]))

    # Merge fallback profiles
    cfg.setdefault("profiles", {})
    for name, prof in PROFILES.items():
        cfg["profiles"].setdefault(name, prof)

    return cfg


def get_data_path(cfg: dict[str, Any] | None = None) -> Path:
    """Return the resolved widget_data.json path."""
    if cfg is None:
        cfg = load_config()
    return Path(cfg["widget_data_source"])


def get_output_dir(cfg: dict[str, Any] | None = None) -> Path:
    """Return the resolved output directory path."""
    if cfg is None:
        cfg = load_config()
    return Path(cfg["output_dir"])

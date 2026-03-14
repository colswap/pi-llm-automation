"""CLI entry point for the Obsidian widget renderer.

Usage:
    python3 -m obsidian_widget_renderer.cli
    python3 -m obsidian_widget_renderer.cli --force
    python3 -m obsidian_widget_renderer.cli --test
    python3 -m obsidian_widget_renderer.cli --profile tall --force
    python3 -m obsidian_widget_renderer.cli --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from .config import load_config, get_data_path, get_output_dir
from .render import render_all

_PKG_DIR = Path(__file__).resolve().parent
_TEST_DATA = _PKG_DIR / "test_data.json"


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="obsidian_widget_renderer",
        description="Render Obsidian widget data into PNG images at multiple ratios.",
    )
    ap.add_argument(
        "--profile",
        choices=["square", "standard", "tall"],
        help="Render a single profile instead of all three.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Skip hash-cache check and render unconditionally.",
    )
    ap.add_argument(
        "--test",
        action="store_true",
        help="Use built-in test data instead of the configured widget_data.json.",
    )
    ap.add_argument(
        "--config",
        metavar="PATH",
        help="Override config.yaml path.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without rendering.",
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    # Load config
    cfg = load_config(args.config)
    data_path = get_data_path(cfg)
    output_dir = get_output_dir(cfg)

    # Determine profiles to render
    profiles = cfg.get("profiles", {})
    if args.profile:
        if args.profile not in profiles:
            print(f"Error: unknown profile '{args.profile}'", file=sys.stderr)
            sys.exit(1)
        profiles = {args.profile: profiles[args.profile]}

    # Test mode: write test data to a temp file
    tmp_path: Path | None = None
    if args.test:
        if not _TEST_DATA.exists():
            print("Error: test_data.json not found in package", file=sys.stderr)
            sys.exit(1)
        tmp_dir = Path(tempfile.mkdtemp(prefix="widget_test_"))
        tmp_path = tmp_dir / "widget_data.json"
        shutil.copy2(_TEST_DATA, tmp_path)
        data_path = tmp_path
        print(f"[test] Using built-in test data: {_TEST_DATA}")

    # Dry-run
    if args.dry_run:
        print("=== Dry Run ===")
        print(f"  Data source : {data_path}")
        print(f"  Output dir  : {output_dir}")
        print(f"  Profiles    : {', '.join(profiles.keys())}")
        print(f"  Force       : {args.force}")
        for name, prof in profiles.items():
            print(f"    {name}: {prof['width']}x{prof['height']} @{prof.get('dpr', 2)}x")
        return

    # Render
    try:
        results = render_all(
            data_path=data_path,
            output_dir=output_dir,
            profiles=profiles,
            force=args.force,
            cfg=cfg,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Render failed: {exc}", file=sys.stderr)
        sys.exit(2)
    finally:
        # Clean up temp test data
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
            tmp_path.parent.rmdir()

    # Summary
    if not results:
        print("No render needed (data unchanged). Use --force to override.")
        return

    print(f"Rendered {len(results)} image(s) to {output_dir}/")
    for name, path in results.items():
        size_kb = path.stat().st_size / 1024
        print(f"  {name}: {path.name} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()

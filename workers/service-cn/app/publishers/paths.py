"""Publish screenshot storage location and safe retrieval.

Shares the data/snapshots root with recordings, but under a separate publish/ subdirectory:
    data/snapshots/publish/{platform}/{YYYYmmdd-HHMMSS}-{tag}.png

Deliberately **does not reuse** main.py's existing `GET /snapshot/{engine}/{filename:path}` route -
that route concatenates user input directly into the path with no traversal protection (`{filename:path}` allows `../`).
Publish screenshots use `GET /publish/screenshot/{platform}/{filename}`, and retrieval uses
`resolve_screenshot()` for a double check.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from ..video_store import get_snapshot_dir

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(s: str, limit: int = 48) -> str:
    return _SAFE.sub("-", (s or "").strip())[:limit] or "x"


def publish_dir(platform: str) -> Path:
    """Screenshot directory. Goes through get_snapshot_dir to reuse its mkdir(parents=True) semantics."""
    return get_snapshot_dir(f"publish/{_slug(platform, 32)}")


def screenshot_path(platform: str, tag: str) -> Tuple[Path, str]:
    """Returns (absolute path, relative identifier for HTTP retrieval).

    File names carry a second-precision timestamp + tag (e.g. 'before-publish' / 'error-dom') - when troubleshooting you need to tell at a glance
    which step of the flow the image was taken at.
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    name = f"{ts}-{_slug(tag)}.png"
    return publish_dir(platform) / name, f"publish/{_slug(platform, 32)}/{name}"


def resolve_screenshot(platform: str, filename: str) -> Optional[Path]:
    """Resolve (platform, filename) into a file that really is inside this platform's screenshot directory.

    Two gates; failing either returns None:
      1) `Path(filename).name` strips all path components - `../../etc/passwd` becomes `passwd`
      2) after resolve(), confirm the parent is the target directory - blocks symlink bypasses
    """
    base = publish_dir(platform).resolve()
    leaf = Path(filename or "").name
    if not leaf or leaf.startswith("."):
        return None
    p = (base / leaf).resolve()
    if p.parent != base or not p.is_file():
        return None
    return p

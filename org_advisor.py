"""AI organization recommendations: suggested folder, reason, confidence.

Never moves anything by itself — the GUI previews suggestions and the
user applies them explicitly. Applied moves are recorded in history with
a restore map so they can be undone.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from categorizer import smart_category
from file_actions import _unique_path
from scanner import FileInfo

_SCREENSHOT_HINTS = ("screenshot", "screen shot", "capture")

# (predicate description, target relative to home, base confidence)
_TARGETS = {
    "Personal": ("Documents/Career" , 90),
    "Finance": ("Documents/Finance", 92),
    "School": ("Documents/School", 85),
    "Work": ("Documents/Work", 80),
    "Pictures": ("Pictures", 75),
    "Videos": ("Videos", 75),
    "Music": ("Music", 75),
    "Programming": ("Projects", 70),
    "Archives": ("Archives", 65),
}


def suggest(f: FileInfo) -> tuple[str, str, int]:
    """Return (suggested folder relative to home, reason, confidence 0-100).

    Empty suggestion means 'leave it where it is'.
    """
    cat, cat_reason = smart_category(f)
    name = f.name.lower()

    if any(h in name for h in _SCREENSHOT_HINTS):
        target, conf = "Pictures/Screenshots", 88
        reason = "Filename indicates a screenshot."
    elif "resume" in name or name.startswith("cv"):
        target, conf = "Documents/Career", 95
        reason = "Appears to be a resume/CV based on the filename."
    elif cat in _TARGETS:
        target, conf = _TARGETS[cat]
        reason = f"Classified as {cat}: {cat_reason}"
        if cat == "Pictures":
            target = f"Pictures/{f.last_modified.year}"
    else:
        return "", "", 0

    # Already in (or under) the right place? Then no suggestion.
    current = str(f.path.parent).replace("\\", "/").lower()
    if current.endswith(target.lower()) or f"/{target.lower()}/" in current + "/":
        return "", "", 0
    # Weak signals lower confidence.
    if f.smart_category == "Other":
        conf -= 20
    return target, reason, max(30, min(99, conf))


def build_suggestions(files: list[FileInfo]) -> list[FileInfo]:
    """Fill suggestion fields in place; return only files with suggestions."""
    out = []
    for f in files:
        f.suggested_folder, f.suggest_reason, f.confidence = suggest(f)
        if f.suggested_folder:
            out.append(f)
    out.sort(key=lambda x: x.confidence, reverse=True)
    return out


def apply_suggestions(files: list[FileInfo],
                      root: Path = None) -> tuple[list[dict], list[tuple]]:
    """Move files to their suggested folders under `root` (default: home).

    Returns (moved records for history [{path,size,restore_from}], failures).
    Each record's restore_from is the NEW location, so restore_files()
    can move it back — that is what powers Undo for organization.
    """
    root = Path(root) if root else Path.home()
    moved: list[dict] = []
    failed: list[tuple] = []
    for f in files:
        if not f.suggested_folder:
            continue
        dest_dir = root / f.suggested_folder
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = _unique_path(dest_dir / f.name)
            shutil.move(str(f.path), str(dest))
            moved.append({"path": str(f.path), "size": f.size_bytes,
                          "restore_from": str(dest)})
            f.path = dest
        except (OSError, shutil.Error) as exc:
            failed.append((f.path, str(exc)))
    return moved, failed

"""One-click actions: each returns a PREVIEW; the GUI confirms and executes.

An action never touches the disk itself — it selects targets and hands
them back with a description, so every action flows through the same
preview -> confirm -> execute -> history pipeline as manual cleanups.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from analytics import DashboardStats
from scanner import FileInfo

_SCREENSHOT_HINTS = ("screenshot", "screen shot", "capture")
_INSTALLER_EXTS = {".exe", ".msi", ".dmg", ".pkg", ".apk", ".iso"}
_TEMP_EXTS = {".tmp", ".log", ".bak", ".crdownload", ".part", ".old"}


@dataclass
class ActionPlan:
    name: str
    description: str
    kind: str                       # delete | archive | organize | rmdir
    files: list[FileInfo] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)


def delete_empty_folders(stats: DashboardStats) -> ActionPlan:
    return ActionPlan(
        name="Delete empty folders",
        description=f"{len(stats.empty_dirs)} empty folder(s) found during "
                    "the scan will be removed.",
        kind="rmdir", dirs=list(stats.empty_dirs))


def old_screenshots(files: list[FileInfo], years: int = 2) -> ActionPlan:
    targets = [f for f in files
               if any(h in f.name.lower() for h in _SCREENSHOT_HINTS)
               and f.days_idle >= years * 365]
    return ActionPlan(
        name=f"Delete screenshots older than {years} years",
        description=f"{len(targets)} old screenshot(s) selected.",
        kind="delete", files=targets)


def clear_temp_and_installers(files: list[FileInfo]) -> ActionPlan:
    targets = [f for f in files
               if f.extension in _TEMP_EXTS or f.extension in _INSTALLER_EXTS]
    return ActionPlan(
        name="Clear temp files & old installers",
        description=f"{len(targets)} temporary file(s) and installer(s) selected.",
        kind="delete", files=targets)


def archive_old_files(files: list[FileInfo], days: int = 365) -> ActionPlan:
    targets = [f for f in files if f.days_idle >= days and f.importance <= 3]
    return ActionPlan(
        name=f"Archive files unused for {days}+ days",
        description=f"{len(targets)} file(s) will be zipped and the originals "
                    "sent to the Recycle Bin.",
        kind="archive", files=targets)


def organize_downloads(files: list[FileInfo]) -> ActionPlan:
    from org_advisor import build_suggestions
    downloads = [f for f in files
                 if "downloads" in (p.lower() for p in f.path.parts)]
    targets = build_suggestions(downloads)
    return ActionPlan(
        name="Organize Downloads",
        description=f"{len(targets)} file(s) in Downloads have a suggested "
                    "destination and will be moved there.",
        kind="organize", files=targets)


def gather_resumes(files: list[FileInfo]) -> ActionPlan:
    targets = [f for f in files
               if "resume" in f.name.lower() or f.name.lower().startswith("cv")]
    for f in targets:
        f.suggested_folder = "Documents/Career"
        f.suggest_reason = "Resume/CV gathered by quick action."
        f.confidence = 95
    return ActionPlan(
        name="Move resumes together",
        description=f"{len(targets)} resume(s) will move to Documents/Career.",
        kind="organize", files=targets)


def remove_empty_dirs(dirs: list[str]) -> tuple[int, list[tuple[str, str]]]:
    """Actually delete empty directories. Returns (removed, failures)."""
    removed, failed = 0, []
    for d in sorted(dirs, key=len, reverse=True):   # deepest first
        try:
            os.rmdir(d)
            removed += 1
        except OSError as exc:
            failed.append((d, str(exc)))
    return removed, failed


ALL_ACTIONS = [
    ("🧹 Delete empty folders", delete_empty_folders, "stats"),
    ("🖼 Delete screenshots older than 2 years", old_screenshots, "files"),
    ("🗑 Clear temp files & old installers", clear_temp_and_installers, "files"),
    ("📦 Archive files unused for 1 year+", archive_old_files, "files"),
    ("⬇ Organize Downloads", organize_downloads, "files"),
    ("📋 Move resumes together", gather_resumes, "files"),
]

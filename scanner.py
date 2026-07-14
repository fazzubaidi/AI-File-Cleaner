"""Find stale files and gather storage statistics.

v3 additions: folder counts, empty folders, unused-age buckets,
oldest/newest tracking, and smart-organization fields on FileInfo.
"""
from __future__ import annotations

import heapq
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from config import (MAX_FILES_PER_SCAN, SCAN_WORKERS, SKIP_DIR_NAMES,
                    STALE_DAYS, category_for)


@dataclass
class FileInfo:
    """Everything the GUI and the AI need to know about one file."""
    path: Path
    size_bytes: int
    last_accessed: datetime
    last_modified: datetime
    explanation: str = field(default="Analyzing...")
    # duplicates / similar
    sha256: Optional[str] = None
    dup_group: Optional[int] = None
    is_dup_keeper: bool = False
    dup_level: str = ""            # "Exact", "Same name", "Near name", ...
    similar_group: Optional[int] = None
    similar_label: str = ""
    # assessment
    recommendation: str = ""
    rec_reason: str = ""
    health: int = 50
    health_reason: str = ""
    importance: int = 3            # 1-5 stars
    importance_reason: str = ""
    # smart organization
    smart_category: str = ""       # School / Programming / Finance / ...
    suggested_folder: str = ""     # e.g. "Documents/Career"
    suggest_reason: str = ""
    confidence: int = 0            # 0-100

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def category(self) -> str:
        return category_for(self.extension)

    @property
    def size_human(self) -> str:
        return human_size(self.size_bytes)

    @property
    def days_idle(self) -> int:
        newest = max(self.last_accessed, self.last_modified)
        return (datetime.now() - newest).days

    @property
    def stars(self) -> str:
        return "★" * self.importance


@dataclass
class RawScanStats:
    """Whole-tree numbers accumulated during the walk (thread-safe via lock)."""
    total_files: int = 0
    total_bytes: int = 0
    total_dirs: int = 0
    empty_dirs: list = field(default_factory=list)
    unused_30: int = 0
    unused_90: int = 0
    unused_365: int = 0
    ext_bytes: Counter = field(default_factory=Counter)
    ext_count: Counter = field(default_factory=Counter)
    cat_bytes: Counter = field(default_factory=Counter)
    folder_bytes: Counter = field(default_factory=Counter)
    folder_files: Counter = field(default_factory=Counter)
    top_files: list = field(default_factory=list)     # heap (size, path)
    oldest: list = field(default_factory=list)        # heap (-mtime, path)
    newest: list = field(default_factory=list)        # heap (mtime, path)

    def add(self, path: Path, size: int, idle_days: int, mtime: float) -> None:
        ext = path.suffix.lower() or "(none)"
        self.total_files += 1
        self.total_bytes += size
        self.ext_bytes[ext] += size
        self.ext_count[ext] += 1
        self.cat_bytes[category_for(ext)] += size
        folder = str(path.parent)
        self.folder_bytes[folder] += size
        self.folder_files[folder] += 1
        if idle_days >= 365:
            self.unused_365 += 1
        if idle_days >= 90:
            self.unused_90 += 1
        if idle_days >= 30:
            self.unused_30 += 1
        _push_top(self.top_files, (size, str(path)), 10)
        _push_top(self.oldest, (-mtime, str(path)), 5)
        _push_top(self.newest, (mtime, str(path)), 5)


def _push_top(heap: list, entry: tuple, cap: int) -> None:
    if len(heap) < cap:
        heapq.heappush(heap, entry)
    elif entry > heap[0]:
        heapq.heapreplace(heap, entry)


def human_size(num_bytes: float) -> str:
    """Format a byte count like '4.2 MB'."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def scan_with_stats(
    folders: Iterable[Path],
    min_age_days: int = STALE_DAYS,
    progress: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    is_excluded: Optional[Callable[[Path], bool]] = None,
) -> tuple[list[FileInfo], RawScanStats]:
    """Scan all folders concurrently.

    Returns (stale files sorted largest-first, whole-tree statistics).
    """
    cutoff = time.time() - min_age_days * 86400
    now = time.time()
    stats = RawScanStats()
    results: list[FileInfo] = []
    lock = threading.Lock()

    def walk_one(folder: Path) -> None:
        for root, dirs, files in os.walk(folder, onerror=lambda e: None):
            if cancel_check and cancel_check():
                return
            root_path = Path(root)
            dirs[:] = [
                d for d in dirs
                if d not in SKIP_DIR_NAMES and not d.startswith(".")
                and not (is_excluded and is_excluded(root_path / d))
            ]
            if progress:
                progress(f"Scanning {root}")
            with lock:
                stats.total_dirs += 1
                if not files and not dirs and root_path not in [Path(f) for f in folders]:
                    stats.empty_dirs.append(str(root_path))
            for fname in files:
                fpath = root_path / fname
                if is_excluded and is_excluded(fpath):
                    continue
                stale, size, st = _stat_file(fpath, cutoff)
                if size < 0 or st is None:
                    continue
                idle = int((now - max(st.st_atime, st.st_mtime)) / 86400)
                with lock:
                    stats.add(fpath, size, idle, st.st_mtime)
                    if stale and len(results) < MAX_FILES_PER_SCAN:
                        results.append(stale)

    real_folders = [Path(f) for f in folders if Path(f).is_dir()]
    with ThreadPoolExecutor(max_workers=max(1, min(SCAN_WORKERS, len(real_folders) or 1))) as pool:
        list(pool.map(walk_one, real_folders))

    results.sort(key=lambda f: f.size_bytes, reverse=True)
    return results, stats


def scan_folders(
    folders: Iterable[Path],
    min_age_days: int = STALE_DAYS,
    progress: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> list[FileInfo]:
    """Backwards-compatible wrapper: stale files only, largest first."""
    stale, _ = scan_with_stats(folders, min_age_days, progress, cancel_check)
    return stale


def _stat_file(fpath: Path, cutoff: float):
    """Stat one file. Returns (FileInfo-if-stale, size or -1, stat or None)."""
    try:
        if fpath.is_symlink():
            return None, -1, None
        st = fpath.stat()
    except (PermissionError, OSError):
        return None, -1, None

    if st.st_atime >= cutoff or st.st_mtime >= cutoff:
        return None, st.st_size, st

    return FileInfo(
        path=fpath,
        size_bytes=st.st_size,
        last_accessed=datetime.fromtimestamp(st.st_atime),
        last_modified=datetime.fromtimestamp(st.st_mtime),
    ), st.st_size, st

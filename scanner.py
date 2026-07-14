"""Find stale files and gather storage statistics."""
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
    path: Path
    size_bytes: int
    last_accessed: datetime
    last_modified: datetime
    explanation: str = field(default="Analyzing...")
    sha256: Optional[str] = None
    dup_group: Optional[int] = None
    is_dup_keeper: bool = False
    similar_group: Optional[int] = None
    similar_label: str = ""
    recommendation: str = ""
    rec_reason: str = ""
    health: int = 50
    health_reason: str = ""

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


@dataclass
class RawScanStats:
    total_files: int = 0
    total_bytes: int = 0
    ext_bytes: Counter = field(default_factory=Counter)
    ext_count: Counter = field(default_factory=Counter)
    cat_bytes: Counter = field(default_factory=Counter)
    folder_bytes: Counter = field(default_factory=Counter)
    top_files: list = field(default_factory=list)

    def add(self, path: Path, size: int) -> None:
        ext = path.suffix.lower() or "(none)"
        self.total_files += 1
        self.total_bytes += size
        self.ext_bytes[ext] += size
        self.ext_count[ext] += 1
        self.cat_bytes[category_for(ext)] += size
        self.folder_bytes[str(path.parent)] += size
        entry = (size, str(path))
        if len(self.top_files) < 10:
            heapq.heappush(self.top_files, entry)
        elif entry > self.top_files[0]:
            heapq.heapreplace(self.top_files, entry)


def human_size(num_bytes: float) -> str:
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
    cutoff = time.time() - min_age_days * 86400
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
            for fname in files:
                fpath = root_path / fname
                if is_excluded and is_excluded(fpath):
                    continue
                stale, size = _stat_file(fpath, cutoff)
                if size < 0:
                    continue
                with lock:
                    stats.add(fpath, size)
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
    stale, _ = scan_with_stats(folders, min_age_days, progress, cancel_check)
    return stale


def _stat_file(fpath: Path, cutoff: float) -> tuple[Optional[FileInfo], int]:
    try:
        if fpath.is_symlink():
            return None, -1
        st = fpath.stat()
    except (PermissionError, OSError):
        return None, -1

    if st.st_atime >= cutoff or st.st_mtime >= cutoff:
        return None, st.st_size

    return FileInfo(
        path=fpath,
        size_bytes=st.st_size,
        last_accessed=datetime.fromtimestamp(st.st_atime),
        last_modified=datetime.fromtimestamp(st.st_mtime),
    ), st.st_size

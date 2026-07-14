"""Storage analytics for the dashboard: totals, type counts, extremes."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from config import TYPE_BUCKETS
from duplicates import DuplicateGroup
from scanner import FileInfo, RawScanStats, human_size


@dataclass
class DashboardStats:
    total_files: int = 0
    total_bytes: int = 0
    total_dirs: int = 0
    empty_dirs: list = field(default_factory=list)
    stale_files: int = 0
    stale_bytes: int = 0
    unused_30: int = 0
    unused_90: int = 0
    unused_365: int = 0
    dup_files: int = 0
    dup_wasted_bytes: int = 0
    reclaimable_bytes: int = 0
    type_counts: dict = field(default_factory=dict)     # label -> (count, bytes)
    unknown_count: int = 0
    largest_file: tuple = ("—", 0)
    largest_folder: tuple = ("—", 0)
    top_files: list = field(default_factory=list)
    top_folders: list = field(default_factory=list)
    oldest_files: list = field(default_factory=list)    # (path, mtime)
    newest_files: list = field(default_factory=list)
    most_duplicated_folders: list = field(default_factory=list)
    ext_breakdown: list = field(default_factory=list)
    cat_breakdown: list = field(default_factory=list)


def build_stats(stale: list[FileInfo], raw: RawScanStats,
                dup_groups: list[DuplicateGroup]) -> DashboardStats:
    """Combine scan output into one dashboard-ready object."""
    s = DashboardStats()
    s.total_files = raw.total_files
    s.total_bytes = raw.total_bytes
    s.total_dirs = raw.total_dirs
    s.empty_dirs = list(raw.empty_dirs)
    s.unused_30, s.unused_90, s.unused_365 = (raw.unused_30, raw.unused_90,
                                              raw.unused_365)
    s.stale_files = len(stale)
    s.stale_bytes = sum(f.size_bytes for f in stale)

    exact = [g for g in dup_groups if g.level in ("Exact", "Identical images")]
    s.dup_files = sum(len(g.files) for g in exact)
    s.dup_wasted_bytes = sum(g.wasted_bytes for g in exact)
    s.reclaimable_bytes = s.stale_bytes - sum(
        g.keeper.size_bytes for g in exact if g.keeper in stale)

    # type buckets for dashboard cards
    known_exts: set[str] = set()
    for label, exts in TYPE_BUCKETS.items():
        count = sum(raw.ext_count.get(e, 0) for e in exts)
        size = sum(raw.ext_bytes.get(e, 0) for e in exts)
        s.type_counts[label] = (count, size)
        known_exts |= exts
    s.unknown_count = sum(c for e, c in raw.ext_count.items()
                          if e not in known_exts)

    if raw.top_files:
        top = sorted(raw.top_files, reverse=True)
        s.top_files = [(Path(p).name, size) for size, p in top]
        s.largest_file = s.top_files[0]
    if raw.folder_bytes:
        s.top_folders = sorted(raw.folder_bytes.items(), key=lambda kv: kv[1],
                               reverse=True)[:10]
        s.largest_folder = s.top_folders[0]
    s.oldest_files = [(p, -neg) for neg, p in sorted(raw.oldest)][:5]
    s.newest_files = [(p, m) for m, p in sorted(raw.newest, reverse=True)][:5]

    dup_folder_counter: Counter = Counter()
    for g in exact:
        for f in g.files:
            if not f.is_dup_keeper:
                dup_folder_counter[str(f.path.parent)] += 1
    s.most_duplicated_folders = dup_folder_counter.most_common(5)

    s.ext_breakdown = sorted(
        ((ext, b, raw.ext_count[ext]) for ext, b in raw.ext_bytes.items()),
        key=lambda t: t[1], reverse=True)[:12]
    s.cat_breakdown = sorted(raw.cat_bytes.items(), key=lambda t: t[1],
                             reverse=True)
    return s


def simulate_cleanup(selected: list[FileInfo], stats: DashboardStats) -> dict:
    """Preview the effect of deleting `selected` (Cleanup Simulator)."""
    freed = sum(f.size_bytes for f in selected)
    dup_removed = sum(1 for f in selected if f.dup_group and not f.is_dup_keeper)
    return {
        "files_removed": len(selected),
        "storage_recovered": freed,
        "storage_recovered_h": human_size(freed),
        "duplicates_removed": dup_removed,
        "remaining_stale": stats.stale_files - len(selected),
        "remaining_stale_bytes_h": human_size(max(0, stats.stale_bytes - freed)),
    }

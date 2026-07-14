"""Storage analytics for the dashboard tab."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from duplicates import DuplicateGroup
from scanner import FileInfo, RawScanStats, human_size


@dataclass
class DashboardStats:
    total_files: int = 0
    total_bytes: int = 0
    stale_files: int = 0
    stale_bytes: int = 0
    dup_files: int = 0
    dup_wasted_bytes: int = 0
    reclaimable_bytes: int = 0
    largest_file: tuple[str, int] = ("—", 0)
    largest_folder: tuple[str, int] = ("—", 0)
    top_files: list[tuple[str, int]] = field(default_factory=list)
    ext_breakdown: list[tuple[str, int, int]] = field(default_factory=list)  # ext, bytes, count
    cat_breakdown: list[tuple[str, int]] = field(default_factory=list)       # category, bytes


def build_stats(stale: list[FileInfo], raw: RawScanStats,
                dup_groups: list[DuplicateGroup]) -> DashboardStats:
    """Combine scan output into one dashboard-ready object."""
    s = DashboardStats()
    s.total_files = raw.total_files
    s.total_bytes = raw.total_bytes
    s.stale_files = len(stale)
    s.stale_bytes = sum(f.size_bytes for f in stale)
    s.dup_files = sum(len(g.files) for g in dup_groups)
    s.dup_wasted_bytes = sum(g.wasted_bytes for g in dup_groups)
    # Reclaimable = everything stale, minus one kept copy per dup group.
    s.reclaimable_bytes = s.stale_bytes - sum(
        g.keeper.size_bytes for g in dup_groups if g.keeper in stale)

    if raw.top_files:
        top = sorted(raw.top_files, reverse=True)
        s.top_files = [(Path(p).name, size) for size, p in top]
        s.largest_file = s.top_files[0]
    if raw.folder_bytes:
        folder, size = max(raw.folder_bytes.items(), key=lambda kv: kv[1])
        s.largest_folder = (folder, size)

    s.ext_breakdown = sorted(
        ((ext, b, raw.ext_count[ext]) for ext, b in raw.ext_bytes.items()),
        key=lambda t: t[1], reverse=True)[:12]
    s.cat_breakdown = sorted(raw.cat_bytes.items(), key=lambda t: t[1],
                             reverse=True)
    return s


def simulate_cleanup(selected: list[FileInfo], stats: DashboardStats) -> dict:
    """Preview the effect of deleting `selected` (Feature: Cleanup Simulator)."""
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

"""Folder health scores: every scanned folder gets a 0-100 rating."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from scanner import FileInfo, RawScanStats, human_size

_TEMP_EXTS = {".tmp", ".log", ".bak", ".crdownload", ".part", ".old"}


@dataclass
class FolderScore:
    folder: str
    score: int
    reasons: list[str] = field(default_factory=list)
    recommendation: str = ""

    @property
    def name(self) -> str:
        return Path(self.folder).name or self.folder


def score_folders(stale: list[FileInfo], raw: RawScanStats,
                  min_files: int = 3) -> list[FolderScore]:
    """Score every folder that holds at least `min_files` scanned files."""
    per_folder: dict[str, list[FileInfo]] = defaultdict(list)
    for f in stale:
        per_folder[str(f.path.parent)].append(f)

    results: list[FolderScore] = []
    for folder, total_count in raw.folder_files.items():
        if total_count < min_files:
            continue
        stale_here = per_folder.get(folder, [])
        stale_n = len(stale_here)
        dup_n = sum(1 for f in stale_here if f.dup_group and not f.is_dup_keeper)
        temp_n = sum(1 for f in stale_here if f.extension in _TEMP_EXTS)
        cats = {f.category for f in stale_here}
        stale_bytes = sum(f.size_bytes for f in stale_here)

        score = 100
        reasons: list[str] = []
        stale_ratio = stale_n / max(total_count, 1)
        if stale_n:
            pen = min(40, int(stale_ratio * 40))
            score -= pen
            reasons.append(f"{stale_n} unused file(s) ({stale_ratio:.0%} of folder)")
        if dup_n:
            score -= min(20, dup_n * 2)
            reasons.append(f"{dup_n} duplicate file(s)")
        if temp_n:
            score -= min(15, temp_n * 3)
            reasons.append(f"{temp_n} temporary file(s)")
        if len(cats) > 4:
            score -= 10
            reasons.append(f"mixed content ({len(cats)} file categories)")
        if stale_bytes > 500 * 1024 * 1024:
            score -= 15
            reasons.append(f"large unused storage ({human_size(stale_bytes)})")

        score = max(0, min(100, score))
        rec = _recommendation(score, dup_n, temp_n, stale_bytes)
        results.append(FolderScore(folder=folder, score=score,
                                   reasons=reasons or ["No issues found"],
                                   recommendation=rec))
    results.sort(key=lambda r: r.score)
    return results


def _recommendation(score: int, dups: int, temps: int, stale_bytes: int) -> str:
    if score >= 85:
        return "Healthy — no action needed."
    parts = []
    if dups:
        parts.append("remove duplicates")
    if temps:
        parts.append("clear temporary files")
    if stale_bytes > 100 * 1024 * 1024:
        parts.append("archive or delete old large files")
    if not parts:
        parts.append("review unused files")
    return "Suggested: " + ", ".join(parts) + "."

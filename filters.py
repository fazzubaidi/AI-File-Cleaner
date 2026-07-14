"""Combinable smart filters for the file table."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config import LARGE_FILE_MB
from scanner import FileInfo


@dataclass
class FilterState:
    """All filters are ANDed together; empty/None fields are inactive."""
    query: str = ""                          # substring of name or path
    extensions: set[str] = field(default_factory=set)   # {".pdf", ".zip"}
    categories: set[str] = field(default_factory=set)
    levels: set[str] = field(default_factory=set)        # recommendation levels
    min_size_mb: Optional[float] = None
    max_size_mb: Optional[float] = None
    modified_over_days: Optional[int] = None
    accessed_over_days: Optional[int] = None
    duplicates_only: bool = False
    similar_only: bool = False
    large_only: bool = False
    recently_modified_days: Optional[int] = None
    old_over_days: Optional[int] = None

    def is_active(self) -> bool:
        return self != FilterState()

    def matches(self, f: FileInfo) -> bool:
        from datetime import datetime
        now = datetime.now()
        if self.query and self.query.lower() not in str(f.path).lower():
            return False
        if self.extensions and f.extension not in self.extensions:
            return False
        if self.categories and f.category not in self.categories:
            return False
        if self.levels and f.recommendation not in self.levels:
            return False
        mb = f.size_bytes / (1024 * 1024)
        if self.min_size_mb is not None and mb < self.min_size_mb:
            return False
        if self.max_size_mb is not None and mb > self.max_size_mb:
            return False
        if self.modified_over_days is not None and \
                (now - f.last_modified).days < self.modified_over_days:
            return False
        if self.accessed_over_days is not None and \
                (now - f.last_accessed).days < self.accessed_over_days:
            return False
        if self.duplicates_only and not f.dup_group:
            return False
        if self.similar_only and not f.similar_group:
            return False
        if self.large_only and mb < LARGE_FILE_MB:
            return False
        if self.recently_modified_days is not None and \
                (now - f.last_modified).days > self.recently_modified_days:
            return False
        if self.old_over_days is not None and f.days_idle < self.old_over_days:
            return False
        return True


def apply_filters(files: list[FileInfo], state: FilterState) -> list[FileInfo]:
    if not state.is_active():
        return list(files)
    return [f for f in files if state.matches(f)]


# Preset filters surfaced in the GUI (Feature: Download Folder Cleanup etc.)
def preset_downloads_cleanup() -> FilterState:
    return FilterState(levels={"Installer", "Temporary", "Duplicate",
                               "Probably Safe"})


def preset_large_files() -> FilterState:
    return FilterState(large_only=True)


def preset_duplicates() -> FilterState:
    return FilterState(duplicates_only=True)

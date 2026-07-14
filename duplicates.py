"""Multi-level duplicate and similar-file detection.

Level 1 — Exact: identical SHA-256 (size-first, thread-pool hashing, cached)
Level 2 — Same name: identical filename in different folders
Level 3 — Near name: version families (Report.pdf / Report (1).pdf / ...)
Level 4 — Identical images: exact-hash groups that are image files
Level 5 — Similar documents: same normalized name stem, same extension,
          sizes within 25% (heuristic — contents are never uploaded)

Hashes are cached in ~/.ai_file_cleaner/hash_cache.json keyed by
path|size|mtime, so unchanged files are never re-hashed across runs.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Optional

from config import HASH_CACHE_FILE, HASH_WORKERS, load_json, save_json
from scanner import FileInfo

_CHUNK = 1 << 20
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".webp",
               ".tiff", ".raw"}
_DOC_EXTS = {".pdf", ".docx", ".doc", ".txt", ".rtf", ".odt", ".pptx",
             ".xlsx", ".md"}


class HashCache:
    """Persistent path|size|mtime -> sha256 cache."""

    def __init__(self):
        self._data: dict[str, str] = load_json(HASH_CACHE_FILE, {})
        self._dirty = False

    @staticmethod
    def _key(info: FileInfo) -> str:
        return f"{info.path}|{info.size_bytes}|{info.last_modified.timestamp():.0f}"

    def get(self, info: FileInfo) -> Optional[str]:
        return self._data.get(self._key(info))

    def put(self, info: FileInfo, digest: str) -> None:
        self._data[self._key(info)] = digest
        self._dirty = True

    def save(self) -> None:
        if self._dirty:
            save_json(HASH_CACHE_FILE, self._data)
            self._dirty = False


def sha256_file(info: FileInfo, cache: Optional[HashCache] = None) -> Optional[str]:
    if cache:
        cached = cache.get(info)
        if cached:
            return cached
    h = hashlib.sha256()
    try:
        with open(info.path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                h.update(chunk)
    except (PermissionError, OSError):
        return None
    digest = h.hexdigest()
    if cache:
        cache.put(info, digest)
    return digest


@dataclass
class DuplicateGroup:
    group_id: int
    sha256: str = ""
    level: str = "Exact"           # Exact | Same name | Near name | Identical images | Similar documents
    files: list[FileInfo] = field(default_factory=list)

    @property
    def keeper(self) -> FileInfo:
        """The 'original': the earliest-modified copy."""
        return min(self.files, key=lambda f: f.last_modified)

    @property
    def wasted_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files) - self.keeper.size_bytes


def find_duplicates(
    files: list[FileInfo],
    cache: Optional[HashCache] = None,
    progress: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> list[DuplicateGroup]:
    """Level 1: exact SHA-256 duplicates. Tags FileInfo in place."""
    cache = cache or HashCache()

    by_size: dict[int, list[FileInfo]] = defaultdict(list)
    for f in files:
        if f.size_bytes > 0:
            by_size[f.size_bytes].append(f)
    candidates = [f for group in by_size.values() if len(group) > 1 for f in group]

    if progress and candidates:
        progress(f"Hashing {len(candidates)} size-collision file(s)...")
    if not (cancel_check and cancel_check()) and candidates:
        with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
            digests = list(pool.map(lambda f: sha256_file(f, cache), candidates))
        for f, digest in zip(candidates, digests):
            f.sha256 = digest
    cache.save()

    by_hash: dict[str, list[FileInfo]] = defaultdict(list)
    for f in candidates:
        if f.sha256:
            by_hash[f.sha256].append(f)

    groups: list[DuplicateGroup] = []
    for digest, members in by_hash.items():
        if len(members) < 2:
            continue
        all_images = all(m.extension in _IMAGE_EXTS for m in members)
        group = DuplicateGroup(
            group_id=len(groups) + 1, sha256=digest,
            level="Identical images" if all_images else "Exact",
            files=members)
        keeper = group.keeper
        for f in members:
            f.dup_group = group.group_id
            f.is_dup_keeper = f is keeper
            f.dup_level = group.level
        groups.append(group)

    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return groups


def find_extended_duplicates(files: list[FileInfo],
                             exact: list[DuplicateGroup]) -> list[DuplicateGroup]:
    """Levels 2, 3, 5 — name-based groups among files NOT already exact dups."""
    next_id = len(exact) + 1
    in_exact = {id(f) for g in exact for f in g.files}
    rest = [f for f in files if id(f) not in in_exact]
    groups: list[DuplicateGroup] = []

    # Level 2: identical filename, different folders.
    by_name: dict[str, list[FileInfo]] = defaultdict(list)
    for f in rest:
        by_name[f.name.lower()].append(f)
    used: set[int] = set()
    for name, members in by_name.items():
        if len(members) > 1:
            g = DuplicateGroup(group_id=next_id, level="Same name", files=members)
            next_id += 1
            groups.append(g)
            used.update(id(m) for m in members)

    # Levels 3 & 5: version families by normalized stem + extension.
    # Exact-dup members may appear as context, but a group only forms when
    # it adds at least one file that is not already an exact duplicate.
    by_stem: dict[tuple[str, str], list[FileInfo]] = defaultdict(list)
    for f in files:
        if id(f) in used:
            continue
        stem = normalize_stem(f.name)
        if stem:
            by_stem[(stem, f.extension)].append(f)
    for (stem, ext), members in by_stem.items():
        fresh = [m for m in members if id(m) not in in_exact]
        if len(members) < 2 or not fresh:
            continue
        sizes = [m.size_bytes for m in members]
        close_sizes = max(sizes) <= min(sizes) * 1.25 if min(sizes) else False
        level = ("Similar documents"
                 if ext in _DOC_EXTS and close_sizes else "Near name")
        g = DuplicateGroup(group_id=next_id, level=level, files=members)
        next_id += 1
        groups.append(g)

    for g in groups:
        keeper = g.keeper
        for f in g.files:
            if f.dup_group is None:
                f.dup_group = g.group_id
                f.is_dup_keeper = f is keeper
                f.dup_level = g.level
    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return groups


_VERSION_TOKENS = re.compile(
    r"(\s*\(\d+\)|\s*-\s*copy(\s*\(\d+\))?|[_\-\s]*(copy|final|draft|new|old|"
    r"updated|edit(ed)?|latest|backup)|[_\-\s]*v?\d{1,3})+$",
    re.IGNORECASE,
)


def normalize_stem(name: str) -> str:
    """Strip version markers: 'Report (1)' / 'Resume_v2' -> 'report' / 'resume'."""
    stem = re.sub(r"\.[^.]+$", "", name)
    stem = _VERSION_TOKENS.sub("", stem)
    return re.sub(r"[\s_\-]+", " ", stem).strip().lower()


@dataclass
class SimilarGroup:
    group_id: int
    label: str
    files: list[FileInfo] = field(default_factory=list)


def find_similar(files: list[FileInfo], threshold: float = 0.86) -> list[SimilarGroup]:
    """Group likely versions of the same document (legacy API, still used)."""
    buckets: dict[tuple[str, str], list[FileInfo]] = defaultdict(list)
    for f in files:
        stem = normalize_stem(f.name)
        if stem:
            buckets[(stem, f.extension)].append(f)

    merged: list[list[FileInfo]] = []
    used: set[tuple[str, str]] = set()
    keys = sorted(buckets)
    for i, key in enumerate(keys):
        if key in used:
            continue
        group = list(buckets[key])
        used.add(key)
        for other in keys[i + 1:]:
            if other in used or other[1] != key[1]:
                continue
            if SequenceMatcher(None, key[0], other[0]).ratio() >= threshold:
                group.extend(buckets[other])
                used.add(other)
        if len(group) > 1:
            merged.append(group)

    groups: list[SimilarGroup] = []
    for members in merged:
        versioned = any(normalize_stem(m.name) != re.sub(
            r"\.[^.]+$", "", m.name).strip().lower() for m in members)
        label = "Multiple versions" if versioned else "Possible duplicate"
        group = SimilarGroup(group_id=len(groups) + 1, label=label, files=members)
        for m in members:
            m.similar_group = group.group_id
            m.similar_label = label
        groups.append(group)
    return groups

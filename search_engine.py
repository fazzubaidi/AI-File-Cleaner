"""Natural-language search over scan results.

Runs fully offline: the query is parsed into category, extension, age,
size, and keyword constraints using a synonym table, then matched against
file names, paths, categories, and AI explanations. Example queries:
'old resumes', 'python projects', 'largest videos', 'unused PDFs'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from scanner import FileInfo

_SYNONYMS = {
    # smart categories
    "school": ("category", "School"), "homework": ("category", "School"),
    "college": ("category", "School"), "class": ("category", "School"),
    "programming": ("category", "Programming"), "code": ("category", "Programming"),
    "project": ("category", "Programming"), "projects": ("category", "Programming"),
    "finance": ("category", "Finance"), "taxes": ("keyword", "tax"),
    "money": ("category", "Finance"),
    "work": ("category", "Work"),
    "picture": ("category", "Pictures"), "pictures": ("category", "Pictures"),
    "photo": ("category", "Pictures"), "photos": ("category", "Pictures"),
    "image": ("category", "Pictures"), "images": ("category", "Pictures"),
    "video": ("category", "Videos"), "videos": ("category", "Videos"),
    "movie": ("category", "Videos"), "movies": ("category", "Videos"),
    "music": ("category", "Music"), "song": ("category", "Music"),
    "songs": ("category", "Music"), "audio": ("category", "Music"),
    "document": ("category", "Documents"), "documents": ("category", "Documents"),
    "download": ("category", "Downloads"), "downloads": ("category", "Downloads"),
    "archive": ("category", "Archives"), "archives": ("category", "Archives"),
    # extensions
    "pdf": ("ext", ".pdf"), "pdfs": ("ext", ".pdf"),
    "python": ("ext", ".py"), "word": ("ext", ".docx"),
    "excel": ("ext", ".xlsx"), "spreadsheet": ("ext", ".xlsx"),
    "powerpoint": ("ext", ".pptx"), "zip": ("ext", ".zip"),
    "zips": ("ext", ".zip"), "installer": ("ext", ".exe"),
    "installers": ("ext", ".exe"), "screenshot": ("keyword", "screenshot"),
    "screenshots": ("keyword", "screenshot"),
    # age / size modifiers
    "old": ("age", 180), "unused": ("age", 90), "ancient": ("age", 730),
    "stale": ("age", 90), "recent": ("recent", 30), "new": ("recent", 30),
    "largest": ("sort", "size"), "biggest": ("sort", "size"),
    "large": ("minmb", 50), "big": ("minmb", 50),
    "oldest": ("sort", "age"), "duplicate": ("dups", True),
    "duplicates": ("dups", True), "duplicated": ("dups", True),
}

_STOPWORDS = {
    "files", "file", "the", "and", "for", "than", "over", "all", "show",
    "find", "give", "list", "used", "use", "opened", "open", "touched",
    "have", "haven't", "hasn't", "has", "had", "not", "been", "was",
    "were", "are", "what", "which", "that", "this", "with", "from",
    "year", "years", "day", "days", "month", "months", "ago",
}

_YEARS = re.compile(r"(\d+)\s*year")


@dataclass
class SearchSpec:
    keywords: list[str] = field(default_factory=list)
    categories: set = field(default_factory=set)
    extensions: set = field(default_factory=set)
    min_idle: int = 0
    max_idle: int = 0
    min_mb: float = 0
    dups_only: bool = False
    sort: str = ""
    description: list[str] = field(default_factory=list)


def parse(query: str) -> SearchSpec:
    spec = SearchSpec()
    q = query.lower().strip()
    m = _YEARS.search(q)
    if m:
        spec.min_idle = int(m.group(1)) * 365
        spec.description.append(f"idle {m.group(1)}+ years")
        q = _YEARS.sub("", q)
    for token in re.findall(r"[a-z0-9']+", q):
        rule = _SYNONYMS.get(token)
        if not rule:
            if len(token) > 2 and token not in _STOPWORDS:
                spec.keywords.append(token)
            continue
        kind, val = rule
        if kind == "category":
            spec.categories.add(val)
            spec.description.append(f"category {val}")
        elif kind == "ext":
            spec.extensions.add(val)
            spec.description.append(f"type {val}")
        elif kind == "age":
            spec.min_idle = max(spec.min_idle, val)
            spec.description.append(f"idle {val}+ days")
        elif kind == "recent":
            spec.max_idle = val
            spec.description.append(f"active in last {val} days")
        elif kind == "minmb":
            spec.min_mb = max(spec.min_mb, val)
            spec.description.append(f"over {val} MB")
        elif kind == "dups":
            spec.dups_only = True
            spec.description.append("duplicates only")
        elif kind == "sort":
            spec.sort = val
            spec.description.append(f"sorted by {val}")
        elif kind == "keyword":
            spec.keywords.append(val)
    return spec


def search(files: list[FileInfo], query: str) -> tuple[list[FileInfo], str]:
    """Return (matching files, human description of what was matched)."""
    spec = parse(query)
    out = []
    for f in files:
        if spec.categories and f.smart_category not in spec.categories:
            continue
        if spec.extensions and f.extension not in spec.extensions:
            continue
        if spec.min_idle and f.days_idle < spec.min_idle:
            continue
        if spec.max_idle and f.days_idle > spec.max_idle:
            continue
        if spec.min_mb and f.size_bytes < spec.min_mb * 1024 * 1024:
            continue
        if spec.dups_only and not f.dup_group:
            continue
        if spec.keywords:
            hay = f"{f.path} {f.explanation} {f.smart_category}".lower()
            # tolerate simple plurals: 'resumes' matches 'resume'
            if not all(k in hay or k.rstrip("s") in hay
                       for k in spec.keywords):
                continue
        out.append(f)
    if spec.sort == "size" or not spec.sort:
        out.sort(key=lambda f: f.size_bytes, reverse=True)
    elif spec.sort == "age":
        out.sort(key=lambda f: f.days_idle, reverse=True)
    desc = ", ".join(spec.description + [f"'{k}'" for k in spec.keywords]) or "all files"
    return out, f"{len(out)} result(s) for {desc}"

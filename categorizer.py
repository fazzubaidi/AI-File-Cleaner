"""Smart categories: classify files into life buckets, not just file types.

Uses filename keywords, extension category, and directory names. Runs
offline; ai_analyzer.py may refine categories when an API key is present.
"""
from __future__ import annotations

from scanner import FileInfo

SMART_CATEGORIES = [
    "School", "Programming", "Finance", "Work", "Personal", "Pictures",
    "Videos", "Music", "Documents", "Downloads", "Archives", "Other",
]

CATEGORY_ICONS = {
    "School": "🎓", "Programming": "💻", "Finance": "💰", "Work": "💼",
    "Personal": "🪪", "Pictures": "🖼", "Videos": "🎬", "Music": "🎵",
    "Documents": "📄", "Downloads": "⬇", "Archives": "📦", "Other": "❓",
}

_KEYWORDS = {
    "Finance": ["tax", "invoice", "receipt", "bank", "statement", "budget",
                "payroll", "w2", "w-2", "1099", "paystub", "finance"],
    "Personal": ["resume", "cv", "passport", "visa", "license", "insurance",
                 "medical", "lease", "contract", "certificate", "diploma",
                 "will", "deed", "id card"],
    "School": ["homework", "assignment", "lecture", "class", "school",
               "college", "university", "exam", "quiz", "study", "essay",
               "lab", "course", "syllabus", "semester", "midterm", "final",
               "thesis", "notes"],
    "Work": ["meeting", "client", "proposal", "quarterly", "報告", "report",
             "presentation", "deliverable", "timesheet", "offer letter"],
    "Programming": ["project", "repo", "src", "code", "script", "sdk",
                    "api", "github"],
}

_EXT_TO_SMART = {
    "Images": "Pictures", "Video": "Videos", "Audio": "Music",
    "Code": "Programming", "Archives": "Archives", "Documents": "Documents",
    "Installers": "Downloads", "Temporary": "Downloads", "Data": "Programming",
}


def smart_category(f: FileInfo) -> tuple[str, str]:
    """Return (category, reason) for one file."""
    hay = f"{f.name} {f.path.parent}".lower()
    for cat, words in _KEYWORDS.items():
        hit = next((w for w in words if w in hay), None)
        if hit:
            # extension can override keyword for obvious media
            if f.category in ("Images", "Video", "Audio") and cat in ("Work", "Programming"):
                break
            return cat, f"Matched keyword '{hit}' in the name or folder path."
    if f.category == "Code":
        return "Programming", f"'{f.extension}' is a source-code extension."
    mapped = _EXT_TO_SMART.get(f.category)
    if mapped:
        return mapped, f"Classified by file type ({f.category})."
    if "downloads" in hay:
        return "Downloads", "Lives in a Downloads folder with no stronger signal."
    return "Other", "No keyword, folder, or extension signal matched."


def apply_categories(files: list[FileInfo]) -> None:
    for f in files:
        f.smart_category, _ = smart_category(f)


def group_by_category(files: list[FileInfo]) -> dict[str, list[FileInfo]]:
    groups: dict[str, list[FileInfo]] = {}
    for f in files:
        if not f.smart_category:
            f.smart_category, _ = smart_category(f)
        groups.setdefault(f.smart_category, []).append(f)
    return {c: groups[c] for c in SMART_CATEGORIES if c in groups}

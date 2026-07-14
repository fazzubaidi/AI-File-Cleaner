"""Rule-based recommendations, health scores, and importance ratings."""
from __future__ import annotations

from config import (ARCHIVE_CANDIDATE_DAYS, IMPORTANCE_LABELS,
                    IMPORTANT_KEYWORDS, LARGE_FILE_MB)
from scanner import FileInfo

_TEMP_EXTS = {".tmp", ".log", ".bak", ".crdownload", ".part", ".old"}
_INSTALLER_EXTS = {".exe", ".msi", ".dmg", ".pkg", ".apk", ".iso"}
_ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}
_PERSONAL_CATS = {"Documents", "Images", "Video", "Audio"}

_CRITICAL_WORDS = ["tax", "passport", "resume", "cv", "legal", "will",
                   "deed", "contract", "insurance", "license", "certificate"]
_IMPORTANT_WORDS = ["project", "homework", "assignment", "report",
                    "research", "thesis", "essay", "presentation"]


def _in_downloads(info: FileInfo) -> bool:
    return "downloads" in (p.lower() for p in info.path.parts)


def recommend(info: FileInfo) -> tuple[str, str]:
    """Return (level, reasoning) for one file."""
    name = info.name.lower()
    idle = info.days_idle
    large = info.size_bytes >= LARGE_FILE_MB * 1024 * 1024

    if any(k in name for k in IMPORTANT_KEYWORDS):
        return "Important", "Filename contains a keyword linked to personal records (resume, tax, invoice...)."
    if info.dup_group and not info.is_dup_keeper:
        return "Duplicate", "Exact byte-for-byte copy of another file; the original is kept."
    if info.extension in _TEMP_EXTS:
        return "Temporary", f"'{info.extension}' files are leftovers from programs or interrupted downloads."
    if info.extension in _INSTALLER_EXTS:
        where = "in Downloads and " if _in_downloads(info) else ""
        return "Installer", f"Installer {where}untouched for {idle} days — usually safe once the app is installed."
    if info.extension in _ARCHIVE_EXTS and _in_downloads(info):
        return "Probably Safe", f"Downloaded archive idle for {idle} days; likely already extracted."
    if large and idle >= ARCHIVE_CANDIDATE_DAYS:
        return "Archive Candidate", f"Large ({info.size_human}) and idle {idle} days — compress it instead of keeping it loose."
    if info.category in _PERSONAL_CATS:
        return "Review Recommended", f"{info.category} may be personal content — check before deleting."
    if idle >= 365:
        return "Probably Safe", f"Untouched for over a year ({idle} days) and not a personal file type."
    return "Review Recommended", f"Idle {idle} days; no strong signal either way — review it."


def health_score(info: FileInfo) -> tuple[int, str]:
    """0–100 usefulness score. High = keep, low = excellent deletion candidate."""
    score = 60
    reasons: list[str] = []
    idle = info.days_idle

    if idle > 365:
        score -= 30
        reasons.append(f"idle {idle}d (-30)")
    elif idle > 180:
        score -= 20
        reasons.append(f"idle {idle}d (-20)")
    elif idle > 90:
        score -= 10
        reasons.append(f"idle {idle}d (-10)")
    else:
        score += 10
        reasons.append(f"idle only {idle}d (+10)")

    if info.dup_group and not info.is_dup_keeper:
        score -= 30
        reasons.append("exact duplicate (-30)")
    if info.extension in _TEMP_EXTS:
        score -= 20
        reasons.append("temporary type (-20)")
    if info.extension in _INSTALLER_EXTS:
        score -= 15
        reasons.append("installer (-15)")
    if any(k in info.name.lower() for k in IMPORTANT_KEYWORDS):
        score += 35
        reasons.append("important keyword (+35)")
    if info.category in _PERSONAL_CATS:
        score += 10
        reasons.append(f"{info.category.lower()} (+10)")
    if info.size_bytes >= LARGE_FILE_MB * 1024 * 1024:
        score -= 5
        reasons.append("large file (-5)")

    score = max(0, min(100, score))
    return score, "; ".join(reasons)


def importance(info: FileInfo) -> tuple[int, str]:
    """1–5 star rating: 5=Critical, 1=Disposable."""
    name = f"{info.name} {info.path.parent}".lower()
    if any(w in name for w in _CRITICAL_WORDS):
        word = next(w for w in _CRITICAL_WORDS if w in name)
        return 5, f"Critical: matches '{word}' — likely a legal, financial, or identity document."
    if any(w in name for w in _IMPORTANT_WORDS):
        word = next(w for w in _IMPORTANT_WORDS if w in name)
        return 4, f"Important: matches '{word}' — likely school or work output."
    if info.extension in _TEMP_EXTS or info.extension in _INSTALLER_EXTS:
        return 1, "Disposable: temporary file or installer, replaceable at any time."
    if info.dup_group and not info.is_dup_keeper:
        return 1, "Disposable: exact duplicate of another file."
    if info.category in ("Images", "Video", "Audio", "Documents"):
        return 3, f"Normal: personal {info.category.lower()} content."
    if info.category == "Other":
        return 2, "Low: unrecognized file type with no importance signals."
    return 2, f"Low: {info.category.lower()} file with no importance signals."


def apply_rules(files: list[FileInfo]) -> None:
    """Fill recommendation, health, and importance for every file."""
    for f in files:
        f.recommendation, f.rec_reason = recommend(f)
        f.health, f.health_reason = health_score(f)
        f.importance, f.importance_reason = importance(f)


def importance_label(stars: int) -> str:
    return IMPORTANCE_LABELS.get(stars, "Normal")

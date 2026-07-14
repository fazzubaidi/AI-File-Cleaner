"""Rule-based cleanup recommendations and file health scores.

These rules always run (offline mode). When a Claude API key is present,
ai_analyzer.py can override the recommendation with model reasoning.

Recommendation levels (config.RECOMMENDATION_LEVELS):
  Safe to Delete / Duplicate / Temporary / Installer / Probably Safe /
  Archive Candidate / Review Recommended / Important
"""
from __future__ import annotations

from config import (ARCHIVE_CANDIDATE_DAYS, IMPORTANT_KEYWORDS,
                    LARGE_FILE_MB)
from scanner import FileInfo

_TEMP_EXTS = {".tmp", ".log", ".bak", ".crdownload", ".part", ".old"}
_INSTALLER_EXTS = {".exe", ".msi", ".dmg", ".pkg", ".apk", ".iso"}
_ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}
_PERSONAL_CATS = {"Documents", "Images", "Video", "Audio"}


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


def apply_rules(files: list[FileInfo]) -> None:
    """Fill recommendation, reasoning, and health score for every file."""
    for f in files:
        f.recommendation, f.rec_reason = recommend(f)
        f.health, f.health_reason = health_score(f)

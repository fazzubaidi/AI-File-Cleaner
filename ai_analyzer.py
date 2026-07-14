"""AI explanations and cleanup recommendations (offline fallback included)."""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

from config import (AI_BATCH_SIZE, AI_CACHE_FILE, AI_MAX_TOKENS,
                    ANTHROPIC_MODEL, RECOMMENDATION_LEVELS, load_json,
                    save_json)
from scanner import FileInfo

_EXTENSION_HINTS: dict[str, str] = {
    ".exe": "Windows program installer or application. Safe to delete if you already installed it or no longer use it.",
    ".msi": "Windows installer package. Usually safe to delete after the program is installed.",
    ".dmg": "macOS disk image installer. Safe to delete once the app is installed.",
    ".zip": "Compressed archive. Safe to delete if you already extracted its contents.",
    ".rar": "Compressed archive. Safe to delete if you already extracted its contents.",
    ".7z": "Compressed archive. Safe to delete if you already extracted its contents.",
    ".iso": "Disc image, often an OS or software installer. Large; safe to delete if no longer needed.",
    ".pdf": "PDF document - could be a receipt, manual, ticket, or report. Check before deleting.",
    ".docx": "Word document. Review it before deleting - may contain your own writing.",
    ".xlsx": "Excel spreadsheet. Review before deleting - may contain your own data.",
    ".pptx": "PowerPoint presentation. Review before deleting.",
    ".txt": "Plain text file, often notes or logs. Quick to review before deleting.",
    ".csv": "Spreadsheet data export. Often a one-time download; usually safe to delete.",
    ".jpg": "Photo or image. Review before deleting - could be a personal photo.",
    ".jpeg": "Photo or image. Review before deleting - could be a personal photo.",
    ".png": "Image, often a screenshot or downloaded graphic. Screenshots are usually safe to delete.",
    ".gif": "Animated image, usually downloaded from the web. Generally safe to delete.",
    ".heic": "iPhone photo. Review before deleting - likely a personal photo.",
    ".mp4": "Video file. Could be personal or downloaded; review before deleting.",
    ".mov": "Video file, often from a phone camera. Review before deleting.",
    ".mp3": "Audio file. Safe to delete if you stream your music.",
    ".wav": "Uncompressed audio, often a recording. Review before deleting.",
    ".tmp": "Temporary file left behind by a program. Almost always safe to delete.",
    ".log": "Program log file used for troubleshooting. Almost always safe to delete.",
    ".bak": "Backup copy of another file. Safe to delete if the original is intact.",
    ".crdownload": "Incomplete Chrome download. Safe to delete.",
    ".part": "Incomplete download. Safe to delete.",
    ".torrent": "Torrent metadata file. Safe to delete once the download finished.",
    ".apk": "Android app installer. Safe to delete if already installed.",
    ".json": "Structured data file used by programs or exports. Usually safe if you don't recognize it.",
    ".html": "Saved web page. Usually safe to delete.",
    ".ics": "Calendar invite file. Safe to delete after the event was added.",
    ".vcf": "Contact card file. Safe to delete after the contact was imported.",
}

_DEFAULT_HINT = "Unrecognized file type. Check the file name and folder for clues before deleting."


def _fallback_explanation(info: FileInfo) -> str:
    base = _EXTENSION_HINTS.get(info.extension, _DEFAULT_HINT)
    if info.dup_group and not info.is_dup_keeper:
        base = f"Exact duplicate of another file (group #{info.dup_group}). " + base
    elif info.similar_group:
        base = f"{info.similar_label} (similar group #{info.similar_group}). " + base
    return base


class AICache:
    def __init__(self):
        self._data: dict[str, dict] = load_json(AI_CACHE_FILE, {})
        self._dirty = False

    @staticmethod
    def _key(info: FileInfo) -> str:
        return f"{info.name}|{info.size_bytes}|{info.last_modified.timestamp():.0f}"

    def get(self, info: FileInfo) -> Optional[dict]:
        return self._data.get(self._key(info))

    def put(self, info: FileInfo, entry: dict) -> None:
        self._data[self._key(info)] = entry
        self._dirty = True

    def save(self) -> None:
        if self._dirty:
            save_json(AI_CACHE_FILE, self._data)
            self._dirty = False


def _get_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except ImportError:
        return None


_SYSTEM_PROMPT = (
    "You help a user clean up old files. For each file you receive (name, "
    "extension, path, size, days idle, duplicate status), respond with a "
    "JSON object mapping each file's id to an object with keys: "
    "'explanation' (one plain-English sentence about what the file likely "
    "is), 'recommendation' (exactly one of: "
    + ", ".join(f"'{lvl}'" for lvl in RECOMMENDATION_LEVELS) +
    "), and 'reason' (one sentence of reasoning). Never claim certainty "
    "about personal documents. Respond ONLY with the JSON object."
)


def _analyze_batch_with_api(client, batch: list[FileInfo]) -> dict[int, dict]:
    listing = [
        {
            "id": i,
            "name": f.name,
            "extension": f.extension,
            "folder": str(f.path.parent),
            "size": f.size_human,
            "days_idle": f.days_idle,
            "exact_duplicate": bool(f.dup_group and not f.is_dup_keeper),
            "similar_versions": f.similar_label or None,
        }
        for i, f in enumerate(batch)
    ]
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=AI_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(listing)}],
    )
    text = response.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    parsed = json.loads(match.group(0) if match else text)
    out: dict[int, dict] = {}
    for k, v in parsed.items():
        if isinstance(v, dict):
            out[int(k)] = v
        else:
            out[int(k)] = {"explanation": str(v)}
    return out


def analyze_files(
    files: list[FileInfo],
    progress: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    client = _get_client()
    cache = AICache()
    if client is None and progress:
        progress("No API key found - using built-in offline explanations.")

    pending = []
    for info in files:
        cached = cache.get(info)
        if cached:
            _apply_entry(info, cached)
        else:
            pending.append(info)

    for start in range(0, len(pending), AI_BATCH_SIZE):
        if cancel_check and cancel_check():
            break
        batch = pending[start:start + AI_BATCH_SIZE]
        if progress:
            progress(f"AI analysis {start + 1}-{start + len(batch)} of {len(pending)}...")

        entries: dict[int, dict] = {}
        if client is not None:
            try:
                entries = _analyze_batch_with_api(client, batch)
            except Exception:
                entries = {}

        for i, info in enumerate(batch):
            entry = entries.get(i) or {"explanation": _fallback_explanation(info)}
            _apply_entry(info, entry)
            if entries.get(i):
                cache.put(info, entry)

    cache.save()


def _apply_entry(info: FileInfo, entry: dict) -> None:
    info.explanation = entry.get("explanation") or _fallback_explanation(info)
    level = entry.get("recommendation")
    if level in RECOMMENDATION_LEVELS:
        info.recommendation = level
        if entry.get("reason"):
            info.rec_reason = str(entry["reason"])

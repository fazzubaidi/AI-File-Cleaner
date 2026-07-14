"""AI explanations, recommendations, and importance — with offline fallback.

v3: explanations must include WHY (evidence from name, folder, age,
duplicate status), and the model also returns an importance rating.
Responses are cached; only names/paths/sizes/dates are ever sent.
"""
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
    ".exe": "a Windows program installer or application",
    ".msi": "a Windows installer package",
    ".dmg": "a macOS disk image installer",
    ".zip": "a compressed archive",
    ".rar": "a compressed archive",
    ".7z": "a compressed archive",
    ".iso": "a disc image, often an OS or software installer",
    ".pdf": "a PDF document — receipt, manual, ticket, or report",
    ".docx": "a Word document",
    ".xlsx": "an Excel spreadsheet",
    ".pptx": "a PowerPoint presentation",
    ".txt": "a plain text file, often notes or logs",
    ".csv": "a spreadsheet data export",
    ".jpg": "a photo or image", ".jpeg": "a photo or image",
    ".png": "an image, often a screenshot or downloaded graphic",
    ".gif": "an animated image from the web",
    ".heic": "an iPhone photo",
    ".mp4": "a video file", ".mov": "a video, often from a phone camera",
    ".mp3": "an audio file", ".wav": "an uncompressed audio recording",
    ".tmp": "a temporary file left behind by a program",
    ".log": "a program log file used for troubleshooting",
    ".bak": "a backup copy of another file",
    ".crdownload": "an incomplete Chrome download",
    ".part": "an incomplete download",
    ".torrent": "a torrent metadata file",
    ".apk": "an Android app installer",
    ".json": "a structured data file used by programs",
    ".html": "a saved web page",
    ".py": "a Python source file",
    ".ics": "a calendar invite file",
    ".vcf": "a contact card file",
}


def _fallback_explanation(info: FileInfo) -> str:
    """Offline explanation that always states its evidence (the WHY)."""
    what = _EXTENSION_HINTS.get(info.extension,
                                "an unrecognized file type")
    evidence = [f"the '{info.extension or 'no'}' extension"]
    folder = info.path.parent.name.lower()
    if folder:
        evidence.append(f"its location in '{info.path.parent.name}'")
    evidence.append(f"{info.days_idle} days without use")
    base = (f"This appears to be {what}, based on {evidence[0]}, "
            f"{evidence[1]}, and {evidence[2]}.")
    if info.dup_group and not info.is_dup_keeper:
        base += (f" It is also a byte-identical copy of another file "
                 f"(duplicate group #{info.dup_group}).")
    elif info.similar_group:
        base += (f" Its name pattern suggests it is one of several versions "
                 f"of the same document ({info.similar_label.lower()}).")
    return base


class AICache:
    """Persistent name|size|mtime -> {explanation, recommendation, ...}."""

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
    "You help a user clean up and organize old files. For each file "
    "(name, extension, path, size, days idle, duplicate status), respond "
    "with a JSON object mapping each file's id to an object with keys: "
    "'explanation' (one or two sentences saying what the file likely is "
    "AND WHY — cite the specific evidence: filename words, extension, "
    "folder, age, duplicate status; e.g. 'This appears to be a resume "
    "because the filename contains Resume and it is a Word document in "
    "Downloads'), 'recommendation' (exactly one of: "
    + ", ".join(f"'{lvl}'" for lvl in RECOMMENDATION_LEVELS) +
    "), 'reason' (one sentence), and 'importance' (integer 1-5, where "
    "5=critical personal/legal/financial document, 3=normal, "
    "1=disposable temp/installer). Never claim certainty about personal "
    "documents. Respond ONLY with the JSON object."
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
        out[int(k)] = v if isinstance(v, dict) else {"explanation": str(v)}
    return out


def analyze_files(
    files: list[FileInfo],
    progress: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    """Fill .explanation and refine recommendation/importance in place."""
    client = _get_client()
    cache = AICache()
    if client is None and progress:
        progress("No API key found — using built-in offline explanations.")

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
            progress(f"AI analysis {start + 1}–{start + len(batch)} of {len(pending)}...")

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
    imp = entry.get("importance")
    if isinstance(imp, int) and 1 <= imp <= 5:
        info.importance = imp

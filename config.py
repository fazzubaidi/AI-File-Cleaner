"""Central configuration, persistent app-data paths, and shared taxonomy."""
from __future__ import annotations

import json
from pathlib import Path

STALE_DAYS = 30

DEFAULT_FOLDERS = [
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home() / "Desktop",
]

SKIP_DIR_NAMES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "AppData", "Library", ".cache", "$RECYCLE.BIN",
    "System Volume Information",
}

MAX_FILES_PER_SCAN = 5000
SCAN_WORKERS = 4
HASH_WORKERS = 4
LARGE_FILE_MB = 100
ARCHIVE_CANDIDATE_DAYS = 180

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
AI_BATCH_SIZE = 25
AI_MAX_TOKENS = 3000

APP_DIR = Path.home() / ".ai_file_cleaner"
SETTINGS_FILE = APP_DIR / "settings.json"
EXCLUSIONS_FILE = APP_DIR / "exclusions.json"
HISTORY_FILE = APP_DIR / "history.json"
HASH_CACHE_FILE = APP_DIR / "hash_cache.json"
AI_CACHE_FILE = APP_DIR / "ai_cache.json"
LOG_FILE = APP_DIR / "app.log"
QUARANTINE_DIR = APP_DIR / "quarantine"


def ensure_app_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> bool:
    try:
        ensure_app_dirs()
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        return True
    except OSError:
        return False


DEFAULT_SETTINGS = {
    "dark_mode": False,
    "delete_mode": "trash",
    "window_geometry": "",
    "group_by_category": False,
    "schedule": {
        "frequency": "none",
        "silent": True,
        "notify_space_gb": 1.0,
        "notify_dup_files": 25,
        "last_run": None,
    },
}


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    stored = load_json(SETTINGS_FILE, {})
    settings.update(stored if isinstance(stored, dict) else {})
    settings["schedule"] = {**DEFAULT_SETTINGS["schedule"],
                            **settings.get("schedule", {})}
    return settings


def save_settings(settings: dict) -> bool:
    return save_json(SETTINGS_FILE, settings)


EXTENSION_CATEGORIES: dict[str, str] = {
    ".pdf": "Documents", ".docx": "Documents", ".doc": "Documents",
    ".txt": "Documents", ".rtf": "Documents", ".odt": "Documents",
    ".pptx": "Documents", ".ppt": "Documents", ".xlsx": "Documents",
    ".xls": "Documents", ".csv": "Documents", ".md": "Documents",
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".gif": "Images",
    ".bmp": "Images", ".heic": "Images", ".webp": "Images", ".svg": "Images",
    ".tiff": "Images", ".raw": "Images",
    ".mp4": "Video", ".mov": "Video", ".avi": "Video", ".mkv": "Video",
    ".wmv": "Video", ".webm": "Video",
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".m4a": "Audio",
    ".ogg": "Audio",
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".gz": "Archives",
    ".exe": "Installers", ".msi": "Installers", ".dmg": "Installers",
    ".pkg": "Installers", ".apk": "Installers", ".iso": "Installers",
    ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code",
    ".css": "Code", ".cpp": "Code", ".c": "Code", ".java": "Code",
    ".ipynb": "Code", ".sh": "Code", ".bat": "Code",
    ".json": "Data", ".xml": "Data", ".yaml": "Data", ".yml": "Data",
    ".sql": "Data", ".db": "Data",
    ".tmp": "Temporary", ".log": "Temporary", ".bak": "Temporary",
    ".crdownload": "Temporary", ".part": "Temporary", ".old": "Temporary",
    ".torrent": "Temporary", ".ics": "Temporary", ".vcf": "Temporary",
}

FILE_CATEGORIES = sorted(set(EXTENSION_CATEGORIES.values()) | {"Other"})

# Extension buckets for the dashboard type cards.
TYPE_BUCKETS = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".webp",
               ".svg", ".tiff", ".raw"},
    "PDFs": {".pdf"},
    "Word docs": {".docx", ".doc", ".rtf", ".odt"},
    "Excel files": {".xlsx", ".xls", ".csv"},
    "Videos": {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "Code files": {".py", ".js", ".ts", ".html", ".css", ".cpp", ".c",
                   ".java", ".ipynb", ".sh", ".bat"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
}


def category_for(extension: str) -> str:
    return EXTENSION_CATEGORIES.get(extension.lower(), "Other")


RECOMMENDATION_LEVELS = [
    "Safe to Delete", "Duplicate", "Temporary", "Installer",
    "Probably Safe", "Archive Candidate", "Review Recommended", "Important",
]

IMPORTANT_KEYWORDS = [
    "resume", "cv", "tax", "invoice", "receipt", "contract", "passport",
    "license", "certificate", "diploma", "insurance", "will", "deed",
    "important", "backup",
]

# Importance stars: 5=Critical ... 1=Disposable
IMPORTANCE_LABELS = {5: "Critical", 4: "Important", 3: "Normal",
                     2: "Low", 1: "Disposable"}

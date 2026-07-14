"""Cleanup history: every deletion/archive is recorded and exportable.

Records for quarantine cleanups keep the original->quarantine mapping,
which powers Undo Last Cleanup / Restore Selected Files.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import HISTORY_FILE, load_json, save_json
from scanner import human_size


def load_history() -> list[dict]:
    data = load_json(HISTORY_FILE, [])
    return data if isinstance(data, list) else []


def add_record(mode: str, files: list[dict], dup_count: int = 0,
               note: str = "") -> dict:
    """Append one cleanup record.

    mode:  trash | quarantine | permanent | archive
    files: [{"path": str, "size": int, "restore_from": str|None}, ...]
    """
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "files": files,
        "count": len(files),
        "total_bytes": sum(f.get("size", 0) for f in files),
        "duplicates_removed": dup_count,
        "restored": False,
        "note": note,
    }
    history = load_history()
    history.append(record)
    save_json(HISTORY_FILE, history)
    return record


def mark_restored(timestamp: str, paths: Optional[set[str]] = None) -> None:
    """Flag a record (or some of its files) as restored."""
    history = load_history()
    for rec in history:
        if rec.get("timestamp") != timestamp:
            continue
        if paths is None:
            rec["restored"] = True
        else:
            for f in rec.get("files", []):
                if f.get("path") in paths:
                    f["restored"] = True
            rec["restored"] = all(f.get("restored") for f in rec.get("files", []))
    save_json(HISTORY_FILE, history)


def last_undoable() -> Optional[dict]:
    """Most recent quarantine or move batch that hasn't been restored."""
    for rec in reversed(load_history()):
        if rec.get("mode") in ("quarantine", "move") and not rec.get("restored"):
            return rec
    return None


def export_csv(path: Path) -> bool:
    """Write the full history to CSV (one row per deleted file)."""
    try:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["timestamp", "mode", "file", "size",
                             "size_human", "duplicates_in_cleanup",
                             "restored"])
            for rec in load_history():
                for f in rec.get("files", []):
                    writer.writerow([
                        rec.get("timestamp"), rec.get("mode"),
                        f.get("path"), f.get("size", 0),
                        human_size(f.get("size", 0)),
                        rec.get("duplicates_removed", 0),
                        f.get("restored", rec.get("restored", False)),
                    ])
        return True
    except OSError:
        return False
